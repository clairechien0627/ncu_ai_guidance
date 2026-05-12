"""RAG pipeline package.

External callers import directly from `rag`:
    from rag import process_pdf
    from rag import count_document_chunks, get_document_language, search_documents
    from rag import delete_document_vectors, update_document_vector_filename
"""
import json
import logging
import os
import re
import time
from typing import Callable, TypedDict

from langchain_community.document_loaders import PyMuPDFLoader
from langchain_experimental.text_splitter import SemanticChunker
from langchain_qdrant import QdrantVectorStore, FastEmbedSparse, RetrievalMode
from langchain_openai import AzureOpenAIEmbeddings, AzureChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_community.document_compressors.flashrank_rerank import FlashrankRerank
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance, VectorParams, SparseVectorParams, Modifier,
    Filter, FieldCondition, MatchValue, MatchAny,
)

from config import settings

logger = logging.getLogger(__name__)

# ── Re-export from sub-modules so `from rag import X` still works ─────────────

from rag.section import (  # noqa: E402,F401
    _EN_SECTION_RE,
    _ZH_SECTION_RE,
    _EXPLICIT_SECTION_KEYWORDS,
    _NUMBERED_HEADING_RE,
    _SECTION_SYSTEM_PROMPT,
    _get_section_llm,
    _extract_headings_by_font,
    _extract_candidate_headings,
    _ZH_SECTION_TO_EN,
    _EN_SECTION_TO_CAT,
    _STRIP_PREFIX_RE,
    _CHAPTER_NUM_RE,
    _CHAPTER_DEFAULT,
    _classify_candidate_text,
    _candidates_to_page_map,
    _extract_headings_from_markdown,
    _build_page_section_map_llm,
    _build_page_section_map_regex,
    _build_page_section_map,
)

from rag.store import (  # noqa: E402,F401
    COLLECTION_NAME,
    VECTOR_SIZE,
    DENSE_NAME,
    SPARSE_NAME,
    RETRIEVAL_K,
    RERANK_TOP_N,
    RERANK_MAX,
    _build_client,
    _ensure_collection,
    _init_vectorstore,
    get_dense_embeddings,
    get_vectorstore,
    get_dense_vectorstore,
    get_reranker,
    get_qdrant_client,
    _lang_from_text,
    get_document_language,
    count_document_chunks,
    delete_document_vectors,
    delete_old_document_vectors,
    delete_pending_document_vectors,
    update_document_vector_filename,
)

from rag.retrieval import (  # noqa: E402,F401
    RetrievedChunk,
    search_documents,
)

from rag.parsers import (  # noqa: E402,F401
    parse_pdf_to_cache,
    retry_llamaparse_warning_pages,
    rebuild_llamaparse_md_from_raw,
    validate_llamaparse_vs_pymupdf,
    _split_cache_pages,
    _read_from_cache,
    _pymupdf_cache_path,
    _llamaparse_cache_path,
    _llamaparse_raw_cache_path,
    _azure_di_cache_path,
    _parse_with_pymupdf4llm,
    _parse_with_azure_di,
    _parse_with_llamaparse,
    _CACHE_PAGE_SEP,
    _LLAMAPARSE_CACHE_DIR,
    _PYMUPDF_CACHE_DIR,
    _AZURE_DI_CACHE_DIR,
)

# ── Quality / page-filter helpers ─────────────────────────────────────────────

def _check_quality(docs: list) -> str | None:
    """Return a quality issue label if the document has extraction problems, else None.

    "scanned"     — almost no text layer (total non-space chars < 50)
    "garbled"     — broken font encoding detected via two signals:
                    (a) CJK+ASCII readable ratio < 30%, OR
                    (b) mid-range Unicode ratio > 20% (U+0080–U+4DFF chars that
                        should rarely appear in legitimate Chinese/English text but
                        dominate when Chinese glyphs are mis-mapped to Latin Extended
                        / IPA / Spacing Modifier blocks)
    "image_heavy" — majority of pages are table/formula/image dominated
    """
    total = 0
    readable = 0
    mid_range = 0
    for doc in docs:
        for ch in doc.page_content:
            if ch.isspace():
                continue
            total += 1
            if ('一' <= ch <= '鿿') or (ch.isascii() and (ch.isalnum() or ch in '.,;:!?()\'"%-+')):
                readable += 1
            elif '' <= ch <= '䷿':
                mid_range += 1

    if total < 50:
        return "scanned"
    if (readable / total) < 0.3 or (mid_range / total) > 0.2:
        return "garbled"

    heavy = sum(1 for doc in docs if _is_table_or_formula_heavy(doc.page_content))
    if docs and heavy / len(docs) >= 0.5:
        return "image_heavy"

    return None


def _clean_text(text: str, lang: str = "zh") -> str:
    """Remove noise introduced by PDF extraction: extra whitespace, blank lines, etc."""
    text = re.sub(r"\*\*==> picture \[.*?\] intentionally omitted <==\*\*\n?", "", text)
    text = re.sub(r"<figcaption>(.*?)</figcaption>", r"\1", text, flags=re.DOTALL)

    def _img_repl(m: re.Match) -> str:
        alt = m.group(1).strip()
        return alt if alt and alt.lower() not in ("image", "figure", "img", "") else ""
    text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", _img_repl, text)

    def _html_img_repl(m: re.Match) -> str:
        alt_m = re.search(r'\balt="([^"]*)"', m.group(0), re.IGNORECASE)
        alt = alt_m.group(1).strip() if alt_m else ""
        return alt if alt and alt.lower() not in ("image", "figure", "img", "") else ""
    text = re.sub(r"<img\b[^>]*/?>", _html_img_repl, text, flags=re.IGNORECASE)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</?(p|div|span|b|i|em|strong|center|sup|sub|figure|figcaption)\b[^>]*>",
                  "", text, flags=re.IGNORECASE)

    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" +\n", "\n", text)
    text = re.sub(r"(?m)^ *\d{1,2} *$", "", text)

    if lang == "zh":
        text = re.sub(r"(?m)^\d{1,2} (?=[^\n]*[一-鿿])[^\n]+\n?", "", text)
    else:
        text = re.sub(r"(?m)^(\d{1,4})\s*$", "", text)

    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


_COVER_PAGE_MARKERS = (
    "國家科學及技術委員會補助",
    "大專學生研究計畫",
    "研究成果報告",
)
_COVER_PAGE_DEFINITIVE = (
    "大專學生研究計畫研究成果報告",
    "執行計畫學生",
    "學生計畫編號",
)


def _is_cover_page(text: str) -> bool:
    """Return True if the page is an NSTC grant report cover/admin page."""
    if any(phrase in text for phrase in _COVER_PAGE_DEFINITIVE):
        return True
    if not any(marker in text for marker in _COVER_PAGE_MARKERS):
        return False
    for line in text.splitlines():
        cjk_count = sum(1 for c in line if '一' <= c <= '鿿')
        if cjk_count >= 25:
            return False
    return True


_HEADING_LINE_ONLY_RE = re.compile(r'^#{1,6}\s+')


def _is_title_page_heading_only(text: str) -> bool:
    """Return True if the page is a heading-only title/cover page with no prose body."""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if not lines:
        return False
    heading_lines = sum(1 for l in lines if _HEADING_LINE_ONLY_RE.match(l))
    body_chars = sum(len(l) for l in lines if not _HEADING_LINE_ONLY_RE.match(l))
    return heading_lines / len(lines) >= 0.85 and body_chars < 120


_REFERENCES_MARKERS = (
    "參考文獻", "參考書目", "引用文獻",
    "Bibliography", "References", "REFERENCES",
)
_REFERENCE_HEADING_LINE_RE = re.compile(
    r"(?im)^\s*(?:#{1,4}\s*)?(?:參考文獻|參考書目|引用文獻|References|Bibliography)\s*$"
)
_REFERENCES_PATTERN = re.compile(
    r"^\s*(?:[\[【\(（]?\d+[\]】\)）\.]|[a-zA-Z][a-zA-Z\-]+,)",
    re.MULTILINE,
)
_BULLETED_REFERENCE_RE = re.compile(
    r"^\s*(?:[✧*•\-]\s*)?[A-Z][A-Za-z'\-]+,\s+[A-Z]",
    re.MULTILINE,
)
_INLINE_BULLETED_REFERENCE_RE = re.compile(
    r"(?:^|\s)[✧*•]\s*[A-Z][A-Za-z'\-]+,\s+[A-Z]"
)
_REFERENCE_CONTINUATION_RE = re.compile(
    r"(?i)\b(?:19|20)\d{2}\b|"
    r"\b(?:journal|chromatogr|chem(?:istry)?|environmental|pollution|talanta|"
    r"spectrom(?:etry)?|microextraction|determination|analysis)\b"
)
_ZH_REFERENCES_PATTERN = re.compile(
    r"[一-鿿]{1,6}[，,：]\s*[《〈]"
    r"|[，,]\s*頁\s*\d"
    r"|[，,]\s*\d{4}\s*年[）)]"
    r"|（\d{4}\s*年\s*[），]"
)


def _is_references_page(text: str) -> bool:
    """Return True if the page is a bibliography/references list."""
    stripped = text.lstrip()
    for marker in _REFERENCES_MARKERS:
        if stripped.startswith(marker) or stripped.startswith(f"# {marker}") or stripped.startswith(f"## {marker}"):
            return True
    lines = [l for l in text.splitlines() if l.strip()]
    if len(lines) < 4:
        return len(_INLINE_BULLETED_REFERENCE_RE.findall(text)) >= 3
    if len(_INLINE_BULLETED_REFERENCE_RE.findall(text)) >= 3:
        return True
    entry_starts = sum(
        1 for l in lines
        if (
            _REFERENCES_PATTERN.search(l)
            or _BULLETED_REFERENCE_RE.search(l)
            or _ZH_REFERENCES_PATTERN.search(l)
        )
    )
    if entry_starts >= 3:
        return True
    return entry_starts / len(lines) >= 0.5


def _is_reference_continuation(text: str) -> bool:
    """Return True for chunks that look like a continuation of bibliography entries."""
    sample = text.strip()[:500]
    if not sample:
        return False
    latin = sum(1 for c in sample if ("A" <= c <= "Z") or ("a" <= c <= "z"))
    cjk = sum(1 for c in sample if "一" <= c <= "鿿")
    if latin < 40 or cjk > latin * 0.25:
        return False
    return bool(_REFERENCE_CONTINUATION_RE.search(sample))


_MATH_UNICODE = frozenset(
    "∑∫∂∇∞≤≥≠≈∝αβγδεζηθλμνξπρστφψωΩΔΓΛΣΦΨ"
    "⁰¹²³⁴⁵⁶⁷⁸⁹₀₁₂₃₄₅₆₇₈₉"
)
_LATEX_DELIMITER_RE = re.compile(
    r'^\s*(?:\$\$|\\\[|\\\]|\\begin\{[^}]+\}|\\end\{[^}]+\}|-{3,})\s*$'
)


def _is_table_or_formula_heavy(text: str) -> bool:
    """Return True if the chunk is predominantly table rows or disconnected formula fragments."""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if len(lines) < 4:
        return False
    pipe_lines = sum(
        1 for l in lines
        if l.count("|") >= 2 and not re.search(r'\\[\(\[]|\\frac|\\leq|\\geq', l)
    )
    if pipe_lines / len(lines) >= 0.35:
        return True
    content_lines = [l for l in lines if not _LATEX_DELIMITER_RE.match(l)]
    if not content_lines:
        return False
    short_lines = sum(1 for l in content_lines if len(l) <= 15)
    if short_lines / len(content_lines) >= 0.55:
        return True
    combined = "".join(lines)
    if combined:
        math_density = sum(1 for c in combined if c in _MATH_UNICODE) / len(combined)
        if math_density >= 0.04:
            return True
    return False


_TOC_LABEL_WORDS = frozenset({
    "目錄", "表目錄", "圖目錄", "圖次", "表次", "附圖目錄", "附表目錄",
    "contents", "table of contents",
    "list of figures", "list of figure",
    "list of tables", "list of table",
    "list of schemes", "list of scheme",
    "list of appendices", "list of appendix",
    "索引",
})

_DOT_LEADER = '[．。·.…\s]'
_MD_TABLE_TOC_RE = re.compile(
    r'\|[^|]*?' + _DOT_LEADER + r'{3,}\s*\d+[^|]*\|',
)
_FIGURE_TABLE_ENTRY_RE = re.compile(
    r'^(?:圖|表)\s*[\d一-九].*' + _DOT_LEADER + r'+\s*\d+\s*$',
)
_MD_FIGURE_LIST_ROW_RE = re.compile(
    r'^\|\s*(?:Figure|Scheme|Table|Fig\.?|圖|表)\s*[\d\-]+',
    re.IGNORECASE,
)
_WORD_BOOKMARK_ERROR = "錯誤! 尚未定義書籤"

_MD_HEADING_RE = re.compile(r'^#{1,3}\s+(.+)$')
_MD_INLINE_RE = re.compile(r'[`*_]')


def _is_toc_page(text: str, lang: str = "zh") -> bool:
    """Return True if the page looks like a table of contents."""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if not lines:
        return False

    if _WORD_BOOKMARK_ERROR in text:
        return True

    for line in lines[:5]:
        clean = re.sub(r'^#+\s*', '', line).strip()
        clean = _MD_INLINE_RE.sub('', clean).strip().lower()
        if clean in _TOC_LABEL_WORDS:
            return True

    md_toc_lines = sum(1 for l in lines if _MD_TABLE_TOC_RE.search(l))
    if len(lines) >= 3 and md_toc_lines / len(lines) >= 0.25:
        return True

    fig_list_rows = sum(1 for l in lines if _MD_FIGURE_LIST_ROW_RE.match(l))
    if len(lines) >= 3 and fig_list_rows / len(lines) >= 0.30:
        return True

    plain_lines = [_MD_INLINE_RE.sub('', l) for l in lines]

    fig_entry_lines = sum(1 for l in plain_lines if _FIGURE_TABLE_ENTRY_RE.match(l.lstrip('`* ')))
    if len(lines) >= 3 and fig_entry_lines / len(lines) >= 0.30:
        return True

    if lang == "en":
        toc_pattern = re.compile(r"[-\.]{4,}\s*\d+\s*$")
        threshold = 0.5
    else:
        toc_pattern = re.compile(
            r'(?:' + _DOT_LEADER + r'{3,}|-{4,})\s*\d+\s*$'
        )
        threshold = 0.40

    toc_lines = sum(1 for l in plain_lines if toc_pattern.search(l))
    return toc_lines / len(lines) >= threshold


def _is_html_data_table_page(text: str) -> bool:
    """Return True if the page is predominantly raw HTML table rows."""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if len(lines) < 6:
        return False
    html_cell = sum(1 for l in lines if re.search(r'<t[rdh]', l, re.IGNORECASE))
    return html_cell / len(lines) >= 0.50


_NMR_PARAM_KEYWORDS = frozenset({
    "EXPNO", "PROCNO", "INSTRUM", "PROBHD", "PULPROG", "TD", "SOLVENT",
    "NS", "SW", "FIDRES", "AQ", "RG", "DW", "DE", "D1", "TDO",
    "NUC1", "P1", "PL1", "SFO1", "SF", "LB", "GB", "PC", "NUCLEUS",
})
_NMR_PARAM_LINE_RE = re.compile(r'^([A-Z][A-Z0-9]{1,9})\s+\S')


def _is_nmr_params_page(text: str) -> bool:
    """Return True if the page is primarily NMR spectrometer parameter listings."""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if not lines:
        return False
    nmr_lines = sum(
        1 for l in lines
        if _NMR_PARAM_LINE_RE.match(l) and l.split()[0] in _NMR_PARAM_KEYWORDS
    )
    return nmr_lines >= 5


# ── Language detection (local, without Qdrant) ───────────────────────────────

def _detect_language(docs: list) -> str:
    """Return 'en' if the document is primarily English, 'zh' otherwise."""
    sample = " ".join(d.page_content for d in docs[:8])
    return _lang_from_text(sample)


# ── Chunking ──────────────────────────────────────────────────────────────────

def _make_splitter(lang: str) -> SemanticChunker:
    """Build a SemanticChunker using Azure text-embedding-3-large."""
    if lang == "en":
        return SemanticChunker(
            embeddings=get_dense_embeddings(),
            breakpoint_threshold_type="percentile",
            breakpoint_threshold_amount=95,
            min_chunk_size=300,
        )
    return SemanticChunker(
        embeddings=get_dense_embeddings(),
        breakpoint_threshold_type="percentile",
        breakpoint_threshold_amount=90,
        min_chunk_size=150,
        sentence_split_regex=r"(?<=[。？！])|(?<=[.?!])\s+",
    )


def _remove_near_duplicate_chunks(chunks: list) -> list:
    """Drop chunks whose Jaccard token similarity ≥ 75% with the previous same-page chunk."""
    if not chunks:
        return chunks
    result = [chunks[0]]
    for chunk in chunks[1:]:
        prev = result[-1]
        if chunk.metadata.get("page") != prev.metadata.get("page"):
            result.append(chunk)
            continue
        curr_tokens = set(chunk.page_content.lower().split())
        prev_tokens = set(prev.page_content.lower().split())
        if not curr_tokens:
            continue
        union = curr_tokens | prev_tokens
        jaccard = len(curr_tokens & prev_tokens) / len(union)
        if jaccard < 0.75:
            result.append(chunk)
    return result


def _split_by_structure_and_semantics(
    docs: list,
    page_section_map: dict[int, str],
    lang: str,
) -> list:
    """Two-level chunking:
    1. Hard boundary at section — no chunk ever crosses section boundaries.
    2. Within each section, pages are grouped into windows (≤ MAX_WINDOW_PAGES)
       and concatenated so chunks can span nearby pages naturally.
    3. Each chunk records page (first page, 0-indexed) and page_end (last page,
       0-indexed) so the caller can display continuous page ranges.
    """
    import re as _re
    from collections import defaultdict
    from langchain_core.documents import Document

    MIN_CHUNK_CHARS = 80
    SEMANTIC_THRESHOLD = 400
    MAX_WINDOW_PAGES = 5
    if lang == "en":
        MERGE_MIN = 600
        SPLIT_MAX = 2500
    else:
        MERGE_MIN = 400
        SPLIT_MAX = 1400

    splitter = _make_splitter(lang)
    splitter.add_start_index = True

    from langchain_text_splitters import RecursiveCharacterTextSplitter
    if lang == "en":
        _fallback_splitter = RecursiveCharacterTextSplitter(
            chunk_size=1500,
            chunk_overlap=150,
            separators=["\n\n", "\n", ". ", " ", ""],
        )
    else:
        _fallback_splitter = RecursiveCharacterTextSplitter(
            chunk_size=800,
            chunk_overlap=80,
            separators=["。\n", "。", "！", "？", "；", "\n\n", "\n", " ", ""],
        )

    section_pages: dict[str, list] = defaultdict(list)
    for doc in docs:
        section = page_section_map.get(doc.metadata.get("page", 0), "unknown")
        section_pages[section].append(doc)

    def _normalise(text: str) -> str:
        return _re.sub(r"\s+", " ", text)

    _HEADING_ONLY_RE = re.compile(r'^\s*#{1,4} [^\n]+\s*$')

    def _is_table_sep_only(text: str) -> bool:
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        if not lines:
            return True
        sep = sum(1 for l in lines if re.match(r'^\|[\-\|\s:]+\|$', l))
        return sep / len(lines) >= 0.8

    def _is_heading_only(text: str) -> bool:
        return bool(_HEADING_ONLY_RE.match(text.strip()))

    def _dedup_overlapping(chunks: list) -> list:
        for i in range(1, len(chunks)):
            prev_lines = [l.strip() for l in chunks[i-1].page_content.splitlines() if l.strip()]
            curr_raw = chunks[i].page_content.splitlines()
            curr_stripped = [l.strip() for l in curr_raw if l.strip()]
            if not prev_lines or not curr_stripped:
                continue
            overlap = 0
            max_check = min(len(prev_lines), len(curr_stripped), 15)
            for j in range(max_check, 0, -1):
                if prev_lines[-j:] == curr_stripped[:j]:
                    overlap = j
                    break
            if overlap > 0:
                new_lines = []
                removed = 0
                for line in curr_raw:
                    if removed < overlap and line.strip():
                        removed += 1
                    else:
                        new_lines.append(line)
                new_text = '\n'.join(new_lines).lstrip('\n')
                if len(new_text.strip()) >= MIN_CHUNK_CHARS:
                    chunks[i] = Document(page_content=new_text, metadata=chunks[i].metadata)
        return chunks

    def _merge_small(chunks: list) -> list:
        if not chunks:
            return chunks
        merged = list(chunks)
        changed = True
        while changed:
            changed = False
            i = 0
            while i < len(merged):
                if len(merged[i].page_content) < MERGE_MIN and len(merged) > 1:
                    sec_i = merged[i].metadata.get("section", "")
                    heading_only = _is_heading_only(merged[i].page_content)
                    can_prev = i > 0 and (merged[i-1].metadata.get("section", "") == sec_i or heading_only)
                    can_next = i < len(merged)-1 and (merged[i+1].metadata.get("section", "") == sec_i or heading_only)
                    if not can_prev and not can_next:
                        i += 1
                        continue
                    if heading_only:
                        j = i + 1 if can_next else i - 1
                    elif can_prev and can_next:
                        j = i - 1 if len(merged[i-1].page_content) <= len(merged[i+1].page_content) else i + 1
                    elif can_prev:
                        j = i - 1
                    else:
                        j = i + 1
                    if j > i:
                        text = merged[i].page_content + "\n\n" + merged[j].page_content
                        pg = min(merged[i].metadata.get("page", 0), merged[j].metadata.get("page", 0))
                        pg_end = max(merged[i].metadata.get("page_end", 0), merged[j].metadata.get("page_end", 0))
                        merged[j] = Document(page_content=text, metadata={**merged[j].metadata, "page": pg, "page_end": pg_end})
                        merged.pop(i)
                    else:
                        text = merged[j].page_content + "\n\n" + merged[i].page_content
                        pg = min(merged[i].metadata.get("page", 0), merged[j].metadata.get("page", 0))
                        pg_end = max(merged[i].metadata.get("page_end", 0), merged[j].metadata.get("page_end", 0))
                        merged[j] = Document(page_content=text, metadata={**merged[j].metadata, "page": pg, "page_end": pg_end})
                        merged.pop(i)
                    changed = True
                else:
                    i += 1
        return merged

    def _split_large(chunks: list) -> list:
        result = []
        for chunk in chunks:
            if len(chunk.page_content) <= SPLIT_MAX:
                result.append(chunk)
                continue
            sub = _fallback_splitter.split_text(chunk.page_content)
            for s in sub:
                if len(s.strip()) >= MIN_CHUNK_CHARS:
                    result.append(Document(page_content=s, metadata=chunk.metadata))
        return result

    def _split_reference_boundaries(chunks: list) -> list:
        result = []
        for chunk in chunks:
            match = _REFERENCE_HEADING_LINE_RE.search(chunk.page_content)
            if not match or match.start() == 0:
                result.append(chunk)
                continue
            before = chunk.page_content[:match.start()].strip()
            refs = chunk.page_content[match.start():].strip()
            if before:
                result.append(Document(page_content=before, metadata=chunk.metadata))
            if refs:
                result.append(Document(
                    page_content=refs,
                    metadata={**chunk.metadata, "section": "references"},
                ))
        return result

    def _retag_reference_chunks(chunks: list) -> list:
        result = []
        in_references = False
        for chunk in chunks:
            is_ref = (
                chunk.metadata.get("section") == "references"
                or _is_references_page(chunk.page_content)
                or (in_references and _is_reference_continuation(chunk.page_content))
            )
            if is_ref:
                in_references = True
                result.append(Document(
                    page_content=chunk.page_content,
                    metadata={**chunk.metadata, "section": "references"},
                ))
            else:
                result.append(chunk)
        return result

    _STRUCT_SPLIT_RE = re.compile(r'(?m)(?=^#{1,3} )')
    _BACKTICK_RE = _re.compile(r'`')

    def _clean_page_text(text: str) -> str:
        return _BACKTICK_RE.sub('', text)

    def _fix_orphaned_punct(text: str) -> str:
        return _re.sub(r'(\S)[ \t]*\n+[ \t]*([。！？；])', r'\1\2\n', text)

    def _section_from_piece_heading(piece: str) -> str | None:
        first_line = piece.lstrip('\n').split('\n')[0].strip()
        m = _MD_HEADING_RE.match(first_line)
        if not m:
            return None
        heading_text = m.group(1)
        cat = _classify_candidate_text(heading_text)
        return cat

    def _chunk_structural_piece(piece: str, base_meta: dict, piece_start: int,
                                boundaries: list, section: str) -> list:
        def _pages_for_pos(start: int, end: int) -> tuple[int, int]:
            pages = [pg for pg, s, e in boundaries if s < end and e > start]
            if not pages:
                return boundaries[0][0], boundaries[-1][0]
            return pages[0], pages[-1]

        section = _section_from_piece_heading(piece) or section

        piece_end = piece_start + len(piece)
        first_pg, last_pg = _pages_for_pos(piece_start, piece_end)
        base = {**base_meta, "section": section, "page": first_pg, "page_end": last_pg}

        if len(piece) < SEMANTIC_THRESHOLD:
            if piece.strip() and not _is_table_sep_only(piece):
                return [Document(page_content=piece, metadata=base)]
            return []

        piece_doc = Document(page_content=piece, metadata=base_meta)
        chunks = []
        search_from = 0
        for chunk in splitter.split_documents([piece_doc]):
            if len(chunk.page_content.strip()) < MIN_CHUNK_CHARS:
                continue
            if _is_table_sep_only(chunk.page_content):
                continue
            key = chunk.page_content[:80]
            local_idx = piece.find(key, search_from)
            if local_idx == -1:
                local_idx = piece.find(key)
            if local_idx == -1:
                piece_norm = _normalise(piece)
                key_norm = _normalise(key)
                ni = piece_norm.find(key_norm, search_from)
                if ni == -1:
                    ni = piece_norm.find(key_norm)
                local_idx = ni
            if local_idx != -1:
                c_start = piece_start + local_idx
                c_end = c_start + len(chunk.page_content)
                search_from = local_idx + len(chunk.page_content)
            else:
                c_start = piece_start + search_from
                c_end = c_start + len(chunk.page_content)
                search_from += len(chunk.page_content)
            fp, lp = _pages_for_pos(c_start, max(c_end, c_start + 1))
            chunk.metadata["section"] = section
            chunk.metadata["page"] = fp
            chunk.metadata["page_end"] = lp
            chunks.append(chunk)
        return chunks

    def _split_window(window_docs: list, section: str) -> list:
        boundaries: list[tuple[int, int, int]] = []
        parts: list[str] = []
        pos = 0
        for doc in window_docs:
            text = _clean_page_text(doc.page_content)
            boundaries.append((doc.metadata.get("page", 0), pos, pos + len(text)))
            parts.append(text)
            pos += len(text) + 2

        combined = _fix_orphaned_punct("\n\n".join(parts))
        base_meta = window_docs[0].metadata

        struct_pieces = _STRUCT_SPLIT_RE.split(combined)
        if not struct_pieces:
            struct_pieces = [combined]

        offsets: list[int] = []
        search_pos = 0
        for piece in struct_pieces:
            idx = combined.find(piece[:40] if len(piece) >= 40 else piece, search_pos)
            offsets.append(idx if idx != -1 else search_pos)
            search_pos = offsets[-1] + len(piece)

        result = []
        for piece, offset in zip(struct_pieces, offsets):
            if not piece.strip():
                continue
            result.extend(_chunk_structural_piece(
                piece, base_meta, offset, boundaries, section
            ))

        result = _merge_small(result)
        result = _dedup_overlapping(result)
        result = _split_large(result)
        return result

    splits = []
    MAX_PAGE_GAP = 5
    for section, s_docs in section_pages.items():
        window_start = 0
        for i in range(1, len(s_docs)):
            prev_pg = s_docs[i - 1].metadata.get("page", 0)
            curr_pg = s_docs[i].metadata.get("page", 0)
            gap_break = (curr_pg - prev_pg) > MAX_PAGE_GAP
            size_break = (i - window_start) >= MAX_WINDOW_PAGES
            if gap_break or size_break:
                splits.extend(_split_window(s_docs[window_start:i], section))
                window_start = i
        splits.extend(_split_window(s_docs[window_start:], section))

    splits = _merge_small(splits)
    splits = _dedup_overlapping(splits)
    splits = _remove_near_duplicate_chunks(splits)
    splits = _split_reference_boundaries(splits)
    splits = _retag_reference_chunks(splits)
    return splits


# ── Abstract extraction ───────────────────────────────────────────────────────

def extract_abstract(docs: list, lang: str) -> str | None:
    """Extract the abstract section from the loaded PDF pages."""
    sample = "\n".join(d.page_content for d in docs[:6])

    if lang == "en":
        heading_re = re.compile(r"(?im)^(abstract|summary)\s*\n+(.+?)(?=\n{2,}[A-Z0-9]|\n{2,}[IVX]+\.|\Z)", re.DOTALL)
    else:
        heading_re = re.compile(r"(?m)^(摘要|Abstract|ABSTRACT)\s*\n+(.+?)(?=\n{2,}[一-龥A-Z0-9]|\Z)", re.DOTALL)

    m = heading_re.search(sample)
    if m:
        return m.group(2).strip()[:2000]

    for doc in docs[:4]:
        for para in doc.page_content.split("\n\n"):
            para = para.strip()
            if len(para) >= 150:
                return para[:2000]
    return None


# ── Main entry point ──────────────────────────────────────────────────────────

def process_pdf(
    file_path: str,
    document_id: int,
    on_stage: "Callable[[str], None] | None" = None,
    reindex_ts: "int | None" = None,
    parser: str = "auto",
    cache_only: bool = False,
) -> tuple[int, str | None, str | None, str]:
    """Load, split, embed (dense+sparse) and store.
    Returns (chunk_count, abstract_text, quality_issue, parser_used).

    parser: "auto" | "pymupdf4llm" | "azure_di" | "llamaparse"
    cache_only: when True, specific parsers must have a cache — no API calls.
                auto mode will only use existing caches + local pymupdf4llm.
    """
    def _stage(label: str) -> None:
        if on_stage:
            on_stage(label)

    _stage("載入 PDF")
    parser_used = parser
    docs: list = []

    if parser != "auto":
        if cache_only:
            docs = _read_from_cache(file_path, parser, document_id)
            if not docs:
                raise ValueError(
                    f"沒有 {parser} 的快取，請先在「解析對比」中執行解析再嵌入"
                )
            parser_used = parser
        else:
            if parser == "azure_di":
                _stage("Azure DI 解析")
                docs = _parse_with_azure_di(file_path, doc_id=document_id)
            elif parser == "llamaparse":
                _stage("LlamaParse 解析")
                docs = _parse_with_llamaparse(file_path, doc_id=document_id)
            elif parser == "pymupdf4llm":
                docs = _parse_with_pymupdf4llm(file_path, doc_id=document_id)
                if not docs:
                    docs = PyMuPDFLoader(file_path).load()
            parser_used = parser

    else:
        azure_cache = _azure_di_cache_path(document_id)
        llama_cache = _llamaparse_cache_path(document_id)

        if os.path.exists(llama_cache):
            docs = _read_from_cache(file_path, "llamaparse", document_id)
            parser_used = "llamaparse"
        elif os.path.exists(azure_cache):
            docs = _read_from_cache(file_path, "azure_di", document_id)
            parser_used = "azure_di"

        if not docs:
            docs = _parse_with_pymupdf4llm(file_path, doc_id=document_id)
            parser_used = "pymupdf4llm"
        if not docs:
            docs = PyMuPDFLoader(file_path).load()
            parser_used = "pymupdf4llm"

    lang = _detect_language(docs)
    logger.info("Detected language for '%s': %s (parser=%s)", os.path.basename(file_path), lang, parser_used)

    for doc in docs:
        doc.page_content = _clean_text(doc.page_content, lang)

    docs = [
        d for d in docs
        if d.page_content
        and not _is_cover_page(d.page_content)
        and not _is_title_page_heading_only(d.page_content)
        and not _is_toc_page(d.page_content, lang)
        and not _is_html_data_table_page(d.page_content)
        and not _is_nmr_params_page(d.page_content)
    ]

    quality_issue = _check_quality(docs)

    if quality_issue is None and any(d.metadata.get("pymupdf4llm") for d in docs):
        md_headings = _extract_headings_from_markdown(docs)
        if len(md_headings) >= 3:
            heading_text = " ".join(h["text"] for h in md_headings)
            total = sum(1 for c in heading_text if not c.isspace())
            if total > 0:
                replacement = heading_text.count('�')
                if replacement / total > 0.25:
                    quality_issue = "garbled"
                    logger.info("pymupdf4llm heading garble detected for '%s' (fffd=%.0f%%)",
                                os.path.basename(file_path), replacement / total * 100)

    if parser == "auto" and quality_issue in ("garbled", "scanned"):
        if cache_only:
            azure_cache = _azure_di_cache_path(document_id)
            llama_cache = _llamaparse_cache_path(document_id)
            if os.path.exists(llama_cache) and parser_used != "llamaparse":
                alt_docs = _read_from_cache(file_path, "llamaparse", document_id)
                fallback = "llamaparse"
            elif os.path.exists(azure_cache) and parser_used != "azure_di":
                alt_docs = _read_from_cache(file_path, "azure_di", document_id)
                fallback = "azure_di"
            else:
                alt_docs = []
                fallback = parser_used
        elif settings.llama_cloud_api_key:
            _stage("LlamaParse 重新解析")
            alt_docs = _parse_with_llamaparse(file_path, doc_id=document_id)
            fallback = "llamaparse"
        elif settings.azure_document_intelligence_endpoint and settings.azure_document_intelligence_key:
            _stage("Azure DI 重新解析")
            alt_docs = _parse_with_azure_di(file_path, doc_id=document_id)
            fallback = "azure_di"
        else:
            alt_docs = []
            fallback = parser_used
        if alt_docs:
            for doc in alt_docs:
                doc.page_content = _clean_text(doc.page_content, lang)
            docs = [
                d for d in alt_docs
                if d.page_content
                and not _is_cover_page(d.page_content)
                and not _is_toc_page(d.page_content, lang)
                and not _is_html_data_table_page(d.page_content)
            ]
            quality_issue = _check_quality(docs)
            parser_used = fallback
            logger.info("%s fallback used; new quality_issue=%s", fallback, quality_issue)

    _stage("分析章節結構")
    page_section_map = _build_page_section_map(docs, lang, file_path=file_path)

    _stage("語意切分")
    splits = _split_by_structure_and_semantics(docs, page_section_map, lang)

    splits.sort(key=lambda s: (s.metadata.get("page", 0), s.metadata.get("page_end", 0)))

    filename = os.path.basename(file_path)
    for idx, split in enumerate(splits):
        split.metadata["document_id"] = str(document_id)
        split.metadata["filename"] = filename
        split.metadata["chunk_index"] = idx
        split.metadata["parser"] = parser_used
        if reindex_ts is not None:
            split.metadata["reindex_ts"] = reindex_ts

    abstract = extract_abstract(docs, lang)

    _stage(f"向量嵌入（{len(splits)} 個 chunk）")
    get_vectorstore().add_documents(splits)
    return len(splits), abstract, quality_issue, parser_used
