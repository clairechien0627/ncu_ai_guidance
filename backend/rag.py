import json
import logging
import os
import re
import time
from typing import Callable, TypedDict
from langfuse import observe
from langfuse.langchain import CallbackHandler

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

COLLECTION_NAME = "reports"
VECTOR_SIZE = 3072       # text-embedding-3-large
DENSE_NAME = "dense"
SPARSE_NAME = "sparse"
RETRIEVAL_K = 10         # per-query retrieval (multi-query yields more total candidates)
RERANK_TOP_N = 4         # baseline chunks sent to LLM (scales with doc count)
RERANK_MAX = 12          # upper bound — reranker singleton is fixed at this size

# ── Section detection ────────────────────────────────────────────────────────

# Fallback regex (only used when LLM extraction fails)
_EN_SECTION_RE = re.compile(
    r"(?im)^(?:\d+\.?\s+|[IVX]+\.\s+)?"
    r"(abstract|introduction|related\s+work|background|"
    r"method(?:s|ology)?|experimental?(?:\s+(?:setup|design|results?))?|"
    r"result(?:s)?(?:\s+and\s+discussion)?|"
    r"discussion|conclusion(?:s)?|future\s+work|"
    r"references|acknowledgements?)\s*$"
)
_ZH_SECTION_RE = re.compile(
    r"(?m)^(?:\d+[\.、]?\s*)?"
    r"(摘要|引言|緒論|前言|相關工作|背景|研究方法|方法|"
    r"實驗(?:設置|設計)?|結果(?:與討論)?|討論|結論|未來工作|參考文獻)\s*$"
)

# Strict heading detector: only accept lines with explicit structural markers.
# Academic section headings virtually always have one of:
#   1. Arabic number prefix  "1.", "2 ", "1、"
#   2. Chinese ordinal       "壹、" "一、" "（一）"
#   3. Roman numeral         "I.", "II."
#   4. Known section keyword alone on the line
_EXPLICIT_SECTION_KEYWORDS = re.compile(
    r"^(?:摘要|大綱|概述|概要|Abstract|ABSTRACT|前言|緒論|引言|背景|Introduction|"
    r"Related\s*Work|Background|Methodology|方法|研究方法|Methods?|"
    r"Results?|結果|結果與討論|討論|Discussion|Conclusion|結論|未來工作|"
    r"Future\s*Work|References|參考文獻|參考書目|Bibliography|"
    r"Acknowledgements?|致謝|謝誌|誌謝|附錄|Appendix)\s*$",
    re.IGNORECASE,
)
_NUMBERED_HEADING_RE = re.compile(
    r"^(?:"
    r"\d{1,2}[\.、\s]\s*[\u4e00-\u9fffA-Za-z]"      # "1. 文獻探討" / "2、方法"
    r"|[壹貳參肆伍陸柒捌玖拾][、。]\s*[\u4e00-\u9fff]"  # "壹、前言"
    r"|[一二三四五六七八九十][、。]\s*[\u4e00-\u9fff]"  # "一、研究動機"
    r"|（[一二三四五六七八九十]）\s*[\u4e00-\u9fff]"    # "（一）研究背景"
    r"|[`]?[一二三四五六七八九十壹貳參肆伍陸柒捌玖拾][`]?\s*\(\s*\)\s*[`]?[\u4e00-\u9fff]"  # "一 ( ) 前言" / "`一` ( ) `前言`"
    r"|[IVX]{1,5}\.\s+[A-Z\u4e00-\u9fff]"             # "I. Introduction"
    r")"
)

_SECTION_SYSTEM_PROMPT = (
    "You are analyzing candidate heading lines extracted from an academic paper. "
    "Your task: identify which lines are genuine major section headings (not subsections, "
    "not figure captions, not table headers), and assign each a standard category.\n\n"
    "Valid categories: abstract, introduction, related_work, background, methods, "
    "results, discussion, conclusion, future_work, references, other\n\n"
    "Return a JSON array only — no other text:\n"
    '[{"page": <int>, "text": "<original text>", "category": "<category>"}]\n\n'
    "If no genuine section headings are found, return []."
)


def _get_section_llm() -> AzureChatOpenAI:
    """Lazy singleton — cheap model for section classification."""
    return AzureChatOpenAI(
        azure_deployment=settings.azure_chat_deployment,
        azure_endpoint=settings.azure_openai_endpoint,
        api_key=settings.azure_openai_api_key,
        api_version=settings.azure_openai_api_version,
        temperature=0,
    )


def _extract_headings_by_font(file_path: str) -> list[dict]:
    """Primary heading detector: use PyMuPDF span-level font size / bold flags.

    Strategy:
    1. Collect all span font sizes across the document to find the body-text size.
    2. A line is a heading candidate if ALL its spans are bold OR the median font
       size of the line is noticeably larger than body text (> 1 pt).
    3. Apply the same length / punctuation guards as the regex fallback.
    """
    try:
        import fitz  # pymupdf
    except ImportError:
        return []

    try:
        pdf = fitz.open(file_path)
    except Exception:
        return []

    # --- Step 1: find body font size ---
    from collections import Counter
    all_sizes: list[float] = []
    for page in pdf:
        for block in page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)["blocks"]:
            if block.get("type") != 0:
                continue
            for line in block["lines"]:
                for span in line["spans"]:
                    if span["text"].strip():
                        all_sizes.append(round(span["size"], 1))
    if not all_sizes:
        pdf.close()
        return []
    body_size = Counter(all_sizes).most_common(1)[0][0]

    # --- Step 2: collect heading candidates ---
    candidates: list[dict] = []
    seen: set[str] = set()

    for page_num, page in enumerate(pdf):
        for block in page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)["blocks"]:
            if block.get("type") != 0:
                continue
            for line in block["lines"]:
                spans = [s for s in line["spans"] if s["text"].strip()]
                if not spans:
                    continue
                line_text = "".join(s["text"] for s in spans).strip()
                if not line_text or line_text in seen:
                    continue
                if len(line_text) > 60:
                    continue
                if line_text.endswith(("。", "，", ",", "；", ".", ";")):
                    continue
                if "，" in line_text:
                    continue

                # Bold: any span has "Bold" or "bold" in font name, or flag bit 4
                is_bold = any(
                    "bold" in s["font"].lower() or bool(s.get("flags", 0) & 16)
                    for s in spans
                )
                # Larger font: median size > body + 1 pt
                median_size = sorted(s["size"] for s in spans)[len(spans) // 2]
                is_larger = median_size > body_size + 1

                if is_bold or is_larger:
                    seen.add(line_text)
                    candidates.append({"page": page_num, "text": line_text})

    pdf.close()
    return candidates


def _extract_candidate_headings(docs: list) -> list[dict]:
    """Regex/keyword fallback: only lines with explicit structural markers."""
    candidates = []
    seen: set[str] = set()
    for doc in docs:
        page_idx = doc.metadata.get("page", 0)
        for line in doc.page_content.splitlines():
            line = line.strip()
            if not line or line in seen:
                continue
            if len(line) > 60:
                continue
            if line.endswith(("。", "，", ",", "；", "：", ".", ";")):
                continue
            if "，" in line:
                continue
            if _EXPLICIT_SECTION_KEYWORDS.match(line) or _NUMBERED_HEADING_RE.match(line):
                seen.add(line)
                candidates.append({"page": page_idx, "text": line})
    return candidates


def _build_page_section_map_llm(docs: list, file_path: str | None = None) -> dict[int, str]:
    """Use LLM to classify candidate headings into standard section categories.

    Tries font-based heading detection first (when file_path provided), then
    falls back to the regex/keyword approach.
    Returns {page_index: category}.  Falls back to regex on any failure.
    """
    is_markdown = any(doc.metadata.get("llamaparse") or doc.metadata.get("pymupdf4llm") for doc in docs)
    if is_markdown:
        candidates = _extract_headings_from_markdown(docs)
    else:
        candidates = (
            _extract_headings_by_font(file_path) if file_path
            else _extract_candidate_headings(docs)
        )
        if not candidates:
            candidates = _extract_candidate_headings(docs)
    if not candidates:
        return {}

    # Cap at 60 candidates to keep the prompt small
    payload = json.dumps(candidates[:60], ensure_ascii=False)
    try:
        llm = _get_section_llm()
        messages = [SystemMessage(content=_SECTION_SYSTEM_PROMPT), HumanMessage(content=payload)]
        response = None
        handler = CallbackHandler()
        for _attempt in range(3):
            try:
                response = llm.invoke(messages)
                break
            except Exception as _exc:
                _exc_str = str(_exc)
                if ("429" in _exc_str or "too_many_requests" in _exc_str.lower()) and _attempt < 2:
                    _wait = 10 * (2 ** _attempt)  # 10s, 20s
                    logger.warning("LLM section extraction rate-limited, retrying in %ds (attempt %d/3)", _wait, _attempt + 1)
                    import time as _time; _time.sleep(_wait)
                else:
                    raise
        if response is None:
            raise RuntimeError("LLM section extraction: no response after retries")
        raw = response.content.strip()
        # Strip markdown code fences that some model versions wrap around JSON
        raw = re.sub(r'^```(?:json)?\s*', '', raw)
        raw = re.sub(r'\s*```$', '', raw)
        sections: list[dict] = json.loads(raw)
    except Exception as exc:
        logger.warning("LLM section extraction failed (%s), falling back to keyword match", exc)
        return _candidates_to_page_map(candidates, docs)

    if not isinstance(sections, list):
        return {}

    # Build page → category, propagate forward for pages between headings
    events: list[tuple[int, str]] = sorted(
        ((s["page"], s["category"]) for s in sections if "page" in s and "category" in s),
        key=lambda x: x[0],
    )
    # The LLM prompt intentionally asks for major headings, but some reports
    # encode important structure as numbered chapter/subsection headings
    # ("第二章 文獻回顧", "2-1-5 相關研究文獻").  Add deterministic matches so
    # those pages do not stay "unknown" when the LLM skips them.
    for c in candidates:
        cat = _classify_candidate_text(c["text"])
        if cat and not any(pg == c["page"] and existing == cat for pg, existing in events):
            events.append((c["page"], cat))
    events.sort(key=lambda x: x[0])
    if not events:
        return {}

    # Heuristic: conclusion / future_work sections never appear in the first 40% of a paper.
    # OCR errors (e.g. "緒論" mis-read as "結論") can misclassify early chapter titles and
    # then drag dozens of pages into the wrong section bucket.
    all_pages = sorted({doc.metadata.get("page", 0) for doc in docs})
    if all_pages:
        early_cutoff = all_pages[int(len(all_pages) * 0.40)]
        _late_only = {"conclusion", "future_work"}
        events = [(pg, cat) for pg, cat in events if cat not in _late_only or pg >= early_cutoff]

    page_map: dict[int, str] = {}
    current = "unknown"  # pages before the first heading get "unknown", not the first section
    event_idx = 0
    for pg in all_pages:
        # Advance current section when we reach a new heading page
        while event_idx < len(events) and events[event_idx][0] <= pg:
            current = events[event_idx][1]
            event_idx += 1
        page_map[pg] = current
    return page_map


_ZH_SECTION_TO_EN: dict[str, str] = {
    "摘要": "abstract",
    "大綱": "introduction",
    "概述": "introduction",
    "概要": "introduction",
    "引言": "introduction",
    "緒論": "introduction",
    "前言": "introduction",
    "相關工作": "related_work",
    "文獻探討": "related_work",
    "文獻回顧": "related_work",
    "相關研究文獻": "related_work",
    "背景": "background",
    "研究背景": "background",
    "研究方法": "methods",
    "材料與方法": "methods",
    "材料與實驗方法": "methods",
    "方法": "methods",
    "實驗": "methods",
    "實驗用品": "methods",
    "實驗儀器": "methods",
    "實驗步驟": "methods",
    "實驗步驟與樣品分析": "methods",
    "實驗設置": "methods",
    "實驗設計": "methods",
    "合成步驟": "methods",
    "合成方法": "methods",
    "儀器設備": "methods",
    "試劑與藥品": "methods",
    "研究設計": "methods",
    "結果": "results",
    "研究結果": "results",
    "結果與討論": "results",
    "討論": "discussion",
    "結論": "conclusion",
    "研究結論": "conclusion",
    "結論與建議": "conclusion",
    "未來工作": "future_work",
    "未來展望": "future_work",
    "參考文獻": "references",
    "參考書目": "references",
    "致謝": "other",
    "謝誌": "other",
    "誌謝": "other",
    "附錄": "other",
}

_EN_SECTION_TO_CAT: dict[str, str] = {
    "abstract": "abstract",
    "introduction": "introduction",
    "related work": "related_work",
    "related works": "related_work",
    "background": "background",
    "methodology": "methods",
    "methods": "methods",
    "method": "methods",
    "experimental setup": "methods",
    "experiments": "methods",
    "results": "results",
    "results and discussion": "results",
    "discussion": "discussion",
    "conclusion": "conclusion",
    "conclusions": "conclusion",
    "future work": "future_work",
    "references": "references",
    "bibliography": "references",
    "acknowledgements": "other",
    "acknowledgments": "other",
    "appendix": "other",
}

_STRIP_PREFIX_RE = re.compile(
    r"^(?:第[一二三四五六七八九十壹貳參肆伍陸柒捌玖拾\d]+章\s*"
    r"|\d{1,2}(?:-\d{1,2})*[\.、\s]?\s*"
    r"|[壹貳參肆伍陸柒捌玖拾][、。]\s*"
    r"|[一二三四五六七八九十][、。]\s*"
    r"|（[一二三四五六七八九十]）\s*"
    r"|\(\s*[一二三四五六七八九十壹貳參肆伍陸柒捌玖拾]\s*\)\s*"
    r"|[一二三四五六七八九十壹貳參肆伍陸柒捌玖拾]\s*\(\s*\)\s*"
    r"|[IVX]{1,5}\.\s+)"
)


_CHAPTER_NUM_RE = re.compile(r'^(\d{1,2})-\d')  # matches "2-1", "2-3-13" but NOT "2." list items

# Standard 4-chapter Chinese thesis: chapter number → default section.
# Only used as a last-resort fallback when no keyword match succeeds, so
# keyword matches (e.g. "文獻探討" → related_work) always take priority.
# Chapter 1 sub-sections (1-1, 1-2 …) are always introduction — even when the
# chapter title page was filtered out and the first visible heading is a sub-section
# with a domain-specific name (e.g. "1-1 非線性光學", "1-2 合成方法").
_CHAPTER_DEFAULT: dict[int, str] = {1: "introduction", 2: "methods", 3: "results", 4: "conclusion"}


def _classify_candidate_text(text: str) -> str | None:
    """Map a heading text to a section category without LLM."""
    # Strip markdown inline formatting (backticks, bold/italic) that pymupdf4llm embeds
    plain = re.sub(r'[`*_]', '', text)

    # Record leading chapter number before stripping prefix (e.g. "2" from "2-3-13 合成步驟")
    _ch_m = _CHAPTER_NUM_RE.match(plain.strip())
    chapter_num = int(_ch_m.group(1)) if _ch_m else None

    stripped = _STRIP_PREFIX_RE.sub("", plain).strip()
    lower = stripped.lower()
    if lower in _EN_SECTION_TO_CAT:
        return _EN_SECTION_TO_CAT[lower]
    for zh, cat in _ZH_SECTION_TO_EN.items():
        if stripped.startswith(zh):
            return cat

    # Last resort: use leading chapter number as a hint.
    # Covers subsections like "2-3-13 合成步驟" when chapter 2 start page was filtered.
    if chapter_num and chapter_num in _CHAPTER_DEFAULT:
        return _CHAPTER_DEFAULT[chapter_num]
    return None


def _candidates_to_page_map(candidates: list[dict], docs: list) -> dict[int, str]:
    """Build page→category map from heading candidates using keyword matching."""
    events: list[tuple[int, str]] = []
    for c in candidates:
        cat = _classify_candidate_text(c["text"])
        if cat:
            events.append((c["page"], cat))
    if not events:
        return {}
    events.sort(key=lambda x: x[0])
    page_map: dict[int, str] = {}
    all_pages = sorted({doc.metadata.get("page", 0) for doc in docs})
    current = "unknown"
    event_idx = 0
    for pg in all_pages:
        while event_idx < len(events) and events[event_idx][0] <= pg:
            current = events[event_idx][1]
            event_idx += 1
        page_map[pg] = current
    return page_map


def _build_page_section_map_regex(docs: list, lang: str) -> dict[int, str]:
    """Regex fallback: scan pages with fixed section keyword patterns.
    Always stores normalised English category names so the retrieval filter works."""
    current = "unknown"
    page_map: dict[int, str] = {}
    if lang == "en":
        for doc in docs:
            page_idx = doc.metadata.get("page", 0)
            m = _EN_SECTION_RE.search(doc.page_content)
            if m:
                raw = m.group(1).strip()
                current = _EN_SECTION_TO_CAT.get(raw.lower(), raw.lower())
            page_map[page_idx] = current
    else:
        # For Chinese, scan each short line through _classify_candidate_text so that
        # formats like `` `一` ( ) `前言` `` (pymupdf4llm backtick-wrapped) are handled.
        for doc in docs:
            page_idx = doc.metadata.get("page", 0)
            for line in doc.page_content.splitlines():
                stripped_line = line.strip()
                if not stripped_line or len(stripped_line) > 80:
                    continue
                cat = _classify_candidate_text(stripped_line)
                if cat:
                    current = cat
                    break
            page_map[page_idx] = current
    return page_map


def _build_page_section_map(docs: list, lang: str, file_path: str | None = None) -> dict[int, str]:
    """Try LLM → keyword fallback → regex fallback."""
    page_map = _build_page_section_map_llm(docs, file_path=file_path)
    if page_map:
        logger.debug("Section map built via LLM/keyword (%d pages tagged)", len(page_map))
        return page_map
    logger.debug("Using regex fallback for section map")
    return _build_page_section_map_regex(docs, lang)


# ── Singleton vectorstore ─────────────────────────────────────────────────────

def _build_client() -> QdrantClient:
    kwargs: dict = {"url": settings.qdrant_url}
    if settings.qdrant_api_key:
        kwargs["api_key"] = settings.qdrant_api_key
    return QdrantClient(**kwargs)


def _ensure_collection(client: QdrantClient) -> None:
    """Create collection with hybrid (dense + sparse) vectors.
    If an old single-vector collection exists, drop and recreate it."""
    if client.collection_exists(COLLECTION_NAME):
        info = client.get_collection(COLLECTION_NAME)
        has_sparse = bool(info.config.params.sparse_vectors)
        if has_sparse:
            return  # already up-to-date
        logger.warning(
            "Collection '%s' is missing sparse vectors — recreating for hybrid search. "
            "Please re-upload all documents.",
            COLLECTION_NAME,
        )
        client.delete_collection(COLLECTION_NAME)

    client.create_collection(
        collection_name=COLLECTION_NAME,
        vectors_config={
            DENSE_NAME: VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
        },
        sparse_vectors_config={
            SPARSE_NAME: SparseVectorParams(modifier=Modifier.IDF),
        },
    )


def _init_vectorstore() -> QdrantVectorStore:
    client = _build_client()
    _ensure_collection(client)

    dense_embeddings = get_dense_embeddings()
    sparse_embeddings = FastEmbedSparse(model_name="Qdrant/bm25")

    return QdrantVectorStore(
        client=client,
        collection_name=COLLECTION_NAME,
        embedding=dense_embeddings,
        sparse_embedding=sparse_embeddings,
        vector_name=DENSE_NAME,
        sparse_vector_name=SPARSE_NAME,
        retrieval_mode=RetrievalMode.HYBRID,
    )


_dense_embeddings: AzureOpenAIEmbeddings | None = None
_vectorstore: QdrantVectorStore | None = None
_dense_vectorstore: QdrantVectorStore | None = None
_reranker: FlashrankRerank | None = None



def get_dense_embeddings() -> AzureOpenAIEmbeddings:
    global _dense_embeddings
    if _dense_embeddings is None:
        _dense_embeddings = AzureOpenAIEmbeddings(
            azure_deployment=settings.azure_embedding_deployment,
            azure_endpoint=settings.azure_openai_endpoint,
            api_key=settings.azure_openai_api_key,
            api_version=settings.azure_openai_api_version,
        )
    return _dense_embeddings


def get_vectorstore() -> QdrantVectorStore:
    global _vectorstore
    if _vectorstore is None:
        _vectorstore = _init_vectorstore()
    return _vectorstore


def get_dense_vectorstore() -> QdrantVectorStore:
    """Dense-only vectorstore — used for English documents where BM25 hurts more than it helps."""
    global _dense_vectorstore
    if _dense_vectorstore is None:
        client = _build_client()
        _ensure_collection(client)
        _dense_vectorstore = QdrantVectorStore(
            client=client,
            collection_name=COLLECTION_NAME,
            embedding=get_dense_embeddings(),
            vector_name=DENSE_NAME,
            retrieval_mode=RetrievalMode.DENSE,
        )
    return _dense_vectorstore


def get_reranker() -> FlashrankRerank:
    global _reranker
    if _reranker is None:
        _reranker = FlashrankRerank(top_n=RERANK_MAX)
    return _reranker


def get_qdrant_client() -> QdrantClient:
    return _build_client()


# ── Ingestion ─────────────────────────────────────────────────────────────────

def _lang_from_text(text: str) -> str:
    """Return 'en' or 'zh' based on CJK vs ASCII-alpha ratio."""
    cjk = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    ascii_alpha = sum(1 for c in text if c.isascii() and c.isalpha())
    total = cjk + ascii_alpha
    if total == 0:
        return "zh"
    return "en" if cjk / total < 0.15 else "zh"


def _detect_language(docs: list) -> str:
    """Return 'en' if the document is primarily English, 'zh' otherwise.

    Samples up to the first 8 pages and compares CJK character count against
    ASCII alpha count.  A document is considered English when CJK characters
    make up less than 15 % of all meaningful characters.
    """
    sample = " ".join(d.page_content for d in docs[:8])
    return _lang_from_text(sample)


def get_document_language(document_ids: list[int] | None = None) -> str:
    """Sample stored chunks from Qdrant to detect document language.

    Returns 'en', 'zh', or 'mixed' (when multiple documents span both languages).
    """
    client = get_qdrant_client()

    scroll_filter = None
    if document_ids:
        scroll_filter = Filter(
            must=[FieldCondition(
                key="metadata.document_id",
                match=MatchAny(any=[str(did) for did in document_ids]),
            )]
        )

    results, _ = client.scroll(
        collection_name=COLLECTION_NAME,
        scroll_filter=scroll_filter,
        limit=20,
        with_payload=True,
        with_vectors=False,
    )

    if not results:
        return "unknown"

    if not document_ids or len(document_ids) <= 1:
        sample = " ".join(
            r.payload.get("page_content", "") for r in results if r.payload
        )
        return _lang_from_text(sample)

    # Multiple documents: detect per-document and report mixed if they differ
    from collections import defaultdict
    doc_texts: dict[str, list[str]] = defaultdict(list)
    for r in results:
        if not r.payload:
            continue
        doc_id = r.payload.get("metadata", {}).get("document_id", "?")
        doc_texts[doc_id].append(r.payload.get("page_content", ""))

    langs = {doc_id: _lang_from_text(" ".join(texts)) for doc_id, texts in doc_texts.items()}
    unique = set(langs.values())
    if len(unique) == 1:
        return unique.pop()
    return "mixed"


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
    mid_range = 0  # U+0080–U+4DFF: suspicious for zh docs
    for doc in docs:
        for ch in doc.page_content:
            if ch.isspace():
                continue
            total += 1
            if ('\u4e00' <= ch <= '\u9fff') or (ch.isascii() and (ch.isalnum() or ch in '.,;:!?()\'"%-+')):
                readable += 1
            elif '\u0080' <= ch <= '\u4dff':
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
    # Strip LlamaParse image-placeholder lines (zero-content noise)
    text = re.sub(r"\*\*==> picture \[.*?\] intentionally omitted <==\*\*\n?", "", text)
    # Unwrap <figcaption> tags — keep the caption text, drop the tags
    text = re.sub(r"<figcaption>(.*?)</figcaption>", r"\1", text, flags=re.DOTALL)
    # Strip markdown image syntax — keep alt text if non-trivial, else discard
    def _img_repl(m: re.Match) -> str:
        alt = m.group(1).strip()
        return alt if alt and alt.lower() not in ("image", "figure", "img", "") else ""
    text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", _img_repl, text)
    # Strip HTML <img> tags — keep alt text if present
    def _html_img_repl(m: re.Match) -> str:
        alt_m = re.search(r'\balt="([^"]*)"', m.group(0), re.IGNORECASE)
        alt = alt_m.group(1).strip() if alt_m else ""
        return alt if alt and alt.lower() not in ("image", "figure", "img", "") else ""
    text = re.sub(r"<img\b[^>]*/?>", _html_img_repl, text, flags=re.IGNORECASE)
    # Strip HTML container tags used around figures — keep text content, remove tags
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</?(p|div|span|b|i|em|strong|center|sup|sub|figure|figcaption)\b[^>]*>",
                  "", text, flags=re.IGNORECASE)

    # Collapse runs of spaces/tabs (but preserve newlines)
    text = re.sub(r"[ \t]+", " ", text)
    # Strip trailing spaces on each line
    text = re.sub(r" +\n", "\n", text)
    # Remove isolated footnote reference numbers (lone digit(s) on their own line)
    text = re.sub(r"(?m)^ *\d{1,2} *$", "", text)

    if lang == "zh":
        # Strip Chinese footnote blocks: digit + Chinese/punctuation content
        # Restricted to lines that contain CJK characters to avoid matching
        # English section headings like "1 Introduction".
        text = re.sub(r"(?m)^\d{1,2} (?=[^\n]*[\u4e00-\u9fff])[^\n]+\n?", "", text)
    else:
        # For English papers: remove lines that look like isolated page headers /
        # footers (short lines of ≤6 words that are purely numeric or a known
        # running-head pattern) but leave section headings intact.
        text = re.sub(r"(?m)^(\d{1,4})\s*$", "", text)  # lone page numbers

    # Collapse 3+ consecutive newlines to at most 2
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


_COVER_PAGE_MARKERS = (
    "國家科學及技術委員會補助",
    "大專學生研究計畫",
    "研究成果報告",
)

# Phrases that appear ONLY on NSTC grant report administrative cover pages.
# If any of these are present the page is unconditionally a cover page
# (no prose-length check needed — the admin text can contain long sentences).
_COVER_PAGE_DEFINITIVE = (
    "大專學生研究計畫研究成果報告",
    "執行計畫學生",
    "學生計畫編號",
)


def _is_cover_page(text: str) -> bool:
    """Return True if the page is an NSTC grant report cover/admin page with no scientific content.

    LlamaParse embeds the report title as a running header on every page, so we must not
    filter out content pages that merely contain the marker in their header.
    We distinguish cover pages by the absence of substantive prose: a real cover page has
    no line with ≥ 25 consecutive CJK characters (administrative fields are all short);
    content pages always have at least one long Chinese sentence.
    """
    # Definitive admin phrases are enough on their own — no branding header required.
    # (Some submissions omit the NSTC letterhead but still have the admin fields.)
    if any(phrase in text for phrase in _COVER_PAGE_DEFINITIVE):
        return True
    if not any(marker in text for marker in _COVER_PAGE_MARKERS):
        return False
    for line in text.splitlines():
        cjk_count = sum(1 for c in line if '\u4e00' <= c <= '\u9fff')
        if cjk_count >= 25:
            return False  # has substantive prose — not a cover page
    return True


_HEADING_LINE_ONLY_RE = re.compile(r'^#{1,6}\s+')


def _is_title_page_heading_only(text: str) -> bool:
    """Return True if the page is a heading-only title/cover page with no prose body.

    Catches:
    - Student-added personal title pages (school name, thesis title, advisor, date)
    - Chapter-title-only pages (e.g. a page with just "# 第一章 緒論" and no body)

    A page is heading-only when ≥ 85 % of its non-empty lines start with `#` markers
    AND the combined body text (non-heading lines) is shorter than 120 characters.
    Content pages that begin with a heading always have substantial prose below it.
    """
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if not lines:
        return False
    heading_lines = sum(1 for l in lines if _HEADING_LINE_ONLY_RE.match(l))
    body_chars = sum(len(l) for l in lines if not _HEADING_LINE_ONLY_RE.match(l))
    return heading_lines / len(lines) >= 0.85 and body_chars < 120


_REFERENCES_MARKERS = (
    # Chinese references section headings
    "參考文獻",
    "參考書目",
    "引用文獻",
    "Bibliography",
    "References",
    "REFERENCES",
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

# Chinese bibliography line patterns:
#   name＋separator＋《/〈 title  (separator can be ，  or full-width ：)
#   ，頁N  (page citation suffix)
#   year in parentheses: （YYYY年）or（YYYY年，
_ZH_REFERENCES_PATTERN = re.compile(
    r"[\u4e00-\u9fff]{1,6}[，,：]\s*[《〈]"    # 姓名，/：《書名》or〈篇名〉
    r"|[，,]\s*頁\s*\d"                         # ，頁NN
    r"|[，,]\s*\d{4}\s*年[）)]"                # ，YYYY年）citation ending
    r"|（\d{4}\s*年\s*[），]"                   # （YYYY年）or（YYYY年，
)


def _is_references_page(text: str) -> bool:
    """Return True if the page is a bibliography/references list with no body content.

    A page is considered a references page if:
    1. It starts with a recognised references heading, OR
    2. The majority of non-empty lines look like bibliography entries
       (numbered/Western citations OR Chinese author-title-year format).
    """
    # Check for section heading at top of page
    stripped = text.lstrip()
    for marker in _REFERENCES_MARKERS:
        if stripped.startswith(marker) or stripped.startswith(f"# {marker}") or stripped.startswith(f"## {marker}"):
            return True

    lines = [l for l in text.splitlines() if l.strip()]
    if len(lines) < 4:
        # Some markdown parsers collapse an entire bibliography page into one
        # long paragraph separated by bullets.
        return len(_INLINE_BULLETED_REFERENCE_RE.findall(text)) >= 3

    if len(_INLINE_BULLETED_REFERENCE_RE.findall(text)) >= 3:
        return True

    # Count lines that START a new reference entry (e.g. [8], [9], or Author, Year)
    # Multi-line bibliography entries have many continuation lines that don't start
    # with a reference marker, so density-per-line is unreliable. Instead, if there
    # are 3+ distinct entry-starting lines, the chunk is a bibliography block.
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

    # Fallback: density check (still catches pages where entries are one line each)
    return entry_starts / len(lines) >= 0.5


def _is_reference_continuation(text: str) -> bool:
    """Return True for chunks that look like a continuation of bibliography entries."""
    sample = text.strip()[:500]
    if not sample:
        return False
    latin = sum(1 for c in sample if ("A" <= c <= "Z") or ("a" <= c <= "z"))
    cjk = sum(1 for c in sample if "\u4e00" <= c <= "\u9fff")
    if latin < 40 or cjk > latin * 0.25:
        return False
    return bool(_REFERENCE_CONTINUATION_RE.search(sample))


_MATH_UNICODE = frozenset(
    "∑∫∂∇∞≤≥≠≈∝αβγδεζηθλμνξπρστφψωΩΔΓΛΣΦΨ"
    "⁰¹²³⁴⁵⁶⁷⁸⁹₀₁₂₃₄₅₆₇₈₉"
)
# LaTeX block-math delimiters — these are structural markers, not content fragments.
# Excluding them from short-line counts avoids flagging math-paper chunks as low-quality.
_LATEX_DELIMITER_RE = re.compile(
    r'^\s*(?:\$\$|\\\[|\\\]|\\begin\{[^}]+\}|\\end\{[^}]+\}|-{3,})\s*$'
)


def _is_table_or_formula_heavy(text: str) -> bool:
    """Return True if the chunk is predominantly table rows or disconnected formula fragments.

    Such chunks are low-quality RAG evidence: table cells without context are
    uninterpretable, and formula fragments extracted from PDFs lose structure.
    They are not hard-filtered but are deprioritised in the ranking step.
    """
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if len(lines) < 4:
        return False

    # Pipe-table rows: lines with ≥ 2 pipe characters.
    # Skip lines that look like LaTeX absolute-value or norm notation inside math mode
    # (those contain \( or \[ alongside the pipes).
    pipe_lines = sum(
        1 for l in lines
        if l.count("|") >= 2 and not re.search(r'\\[\(\[]|\\frac|\\leq|\\geq', l)
    )
    if pipe_lines / len(lines) >= 0.35:
        return True

    # Very short-line fragmentation (< 15 chars per line) indicates table cells or
    # broken content lines — but LaTeX block delimiters ($$, \[, \]) are intentionally
    # short and must not be counted, or every math-paper chunk gets flagged.
    content_lines = [l for l in lines if not _LATEX_DELIMITER_RE.match(l)]
    if not content_lines:
        return False
    short_lines = sum(1 for l in content_lines if len(l) <= 15)
    if short_lines / len(content_lines) >= 0.55:
        return True

    # High density of math Unicode symbols (not common ASCII +/=)
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


_DOT_LEADER = '[．。·.…\s]'   # dot-leader chars: fullwidth/ideographic/middle-dot, ASCII period, U+2026 (…), whitespace
_MD_TABLE_TOC_RE = re.compile(
    # Markdown table row with dot-leaders + page number inside a cell.
    # Use non-greedy [^|]*? so the dot cluster is found before end of cell.
    r'\|[^|]*?' + _DOT_LEADER + r'{3,}\s*\d+[^|]*\|',
)
_FIGURE_TABLE_ENTRY_RE = re.compile(
    # Figure/table list entry: starts with 圖/表 N and ends with dot-leaders + page number.
    # Content-page references (e.g. "圖4-9(b) 為...") do NOT end with a page number.
    r'^(?:圖|表)\s*[\d一-九].*' + _DOT_LEADER + r'+\s*\d+\s*$',
)
# Markdown table row where first cell is a Figure/Scheme/Table reference (no dot-leaders).
# Matches both header rows and data rows of English figure-list tables.
_MD_FIGURE_LIST_ROW_RE = re.compile(
    r'^\|\s*(?:Figure|Scheme|Table|Fig\.?|圖|表)\s*[\d\-]+',
    re.IGNORECASE,
)
# Word exports broken TOC entries when the target bookmark was undefined.
_WORD_BOOKMARK_ERROR = "錯誤! 尚未定義書籤"


def _is_toc_page(text: str, lang: str = "zh") -> bool:
    """Return True if the page looks like a table of contents (mostly dot leaders + numbers)."""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if not lines:
        return False

    # Word-exported TOC pages with unresolved bookmark references are definitively TOC.
    if _WORD_BOOKMARK_ERROR in text:
        return True

    # Detect pages whose heading is explicitly a TOC/index label (目錄, 表目錄, 圖目錄…).
    # These pages (first ~5 lines) may not have enough dot-leader lines to hit the
    # density threshold, but they are definitively non-content.
    for line in lines[:5]:
        clean = re.sub(r'^#+\s*', '', line).strip()
        clean = _MD_INLINE_RE.sub('', clean).strip().lower()
        if clean in _TOC_LABEL_WORDS:
            return True

    # Detect markdown-table-formatted TOC content (pymupdf4llm renders some TOC pages
    # as pipe-delimited tables; the $ anchor on toc_pattern misses rows ending with |).
    md_toc_lines = sum(1 for l in lines if _MD_TABLE_TOC_RE.search(l))
    if len(lines) >= 3 and md_toc_lines / len(lines) >= 0.25:
        return True

    # Detect "List of Figures / Schemes / Tables" formatted as plain markdown tables
    # (no dot-leaders — just | Figure 1-1 | description | 28 | style rows).
    fig_list_rows = sum(1 for l in lines if _MD_FIGURE_LIST_ROW_RE.match(l))
    if len(lines) >= 3 and fig_list_rows / len(lines) >= 0.30:
        return True

    plain_lines = [_MD_INLINE_RE.sub('', l) for l in lines]

    # Detect 圖目錄 / 表目錄 content pages: many lines start with a figure/table reference
    # (these pages often have multi-line captions so the dot-leader at end-of-line is rare).
    fig_entry_lines = sum(1 for l in plain_lines if _FIGURE_TABLE_ENTRY_RE.match(l.lstrip('`* ')))
    if len(lines) >= 3 and fig_entry_lines / len(lines) >= 0.30:
        return True

    if lang == "en":
        # English TOC lines use repeated dots OR dashes as leaders:
        #   "Introduction ........ 3"  or  "Introduction ----------- 3"
        toc_pattern = re.compile(r"[-\.]{4,}\s*\d+\s*$")
        threshold = 0.5  # stricter: need 50 %+ of lines to match
    else:
        # Chinese TOC leaders: dot-style (．．．15) OR dash-style (----------15).
        # Require 4+ consecutive dashes to avoid matching single dashes in content.
        toc_pattern = re.compile(
            r'(?:' + _DOT_LEADER + r'{3,}|-{4,})\s*\d+\s*$'
        )
        threshold = 0.40

    toc_lines = sum(1 for l in plain_lines if toc_pattern.search(l))
    return toc_lines / len(lines) >= threshold


def _is_html_data_table_page(text: str) -> bool:
    """Return True if the page is predominantly raw HTML table rows.

    Pages that are almost entirely <tr>/<td> markup (e.g. large patient-data
    listings or measurement tables) produce dozens of near-identical chunks
    with no retrievable semantic content.  Filter them out at the page level.
    """
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
    """Return True if the page is primarily NMR spectrometer parameter listings.

    Bruker/JEOL NMR systems export per-spectrum parameter blocks that look like:
        INSTRUM  300BB
        PULPROG  zg30
        TD       16384
        SOLVENT  CDCl3
    These are appended to appendix pages. They have zero RAG value.
    """
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if not lines:
        return False
    nmr_lines = sum(
        1 for l in lines
        if _NMR_PARAM_LINE_RE.match(l) and l.split()[0] in _NMR_PARAM_KEYWORDS
    )
    return nmr_lines >= 5


def _make_splitter(lang: str) -> SemanticChunker:
    """Build a SemanticChunker using Azure text-embedding-3-large for speed."""
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
    """Drop chunks whose Jaccard token similarity ≥ 75 % with the previous same-page chunk.

    Uses symmetric Jaccard similarity (intersection / union) instead of one-sided
    coverage so that a short chunk whose vocabulary is a subset of a larger chunk
    is NOT incorrectly dropped — only truly near-identical fragments are removed.

    Handles pages like long Python variable-name lists or raw data tables that
    get sliced into many nearly-identical fragments by the semantic chunker.
    Keeps the first occurrence; drops all subsequent near-duplicates.
    """
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
        MERGE_MIN = 600   # English paragraphs are naturally longer
        SPLIT_MAX = 2500
    else:
        MERGE_MIN = 400
        SPLIT_MAX = 1400

    splitter = _make_splitter(lang)
    splitter.add_start_index = True

    # Fallback splitter for oversized chunks
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
        """True if the chunk is almost entirely markdown table separator rows (|---|---|)."""
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        if not lines:
            return True
        sep = sum(1 for l in lines if re.match(r'^\|[\-\|\s:]+\|$', l))
        return sep / len(lines) >= 0.8

    def _is_heading_only(text: str) -> bool:
        """True if the chunk is just a heading line with no body."""
        return bool(_HEADING_ONLY_RE.match(text.strip()))

    def _dedup_overlapping(chunks: list) -> list:
        """Remove lines at the start of chunk[i] that duplicate lines at the end of chunk[i-1].
        Handles table rows repeated at page boundaries by PyMuPDF."""
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
        """Merge chunks shorter than MERGE_MIN into their shorter same-section neighbour.
        Heading-only chunks may merge with adjacent chunks across section boundaries."""
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
                    # Heading-only chunks may merge across sections (prefer next)
                    can_prev = i > 0 and (merged[i-1].metadata.get("section", "") == sec_i or heading_only)
                    can_next = i < len(merged)-1 and (merged[i+1].metadata.get("section", "") == sec_i or heading_only)
                    if not can_prev and not can_next:
                        i += 1
                        continue
                    if heading_only:
                        # Heading labels belong with the content that follows
                        j = i + 1 if can_next else i - 1
                    elif can_prev and can_next:
                        j = i - 1 if len(merged[i-1].page_content) <= len(merged[i+1].page_content) else i + 1
                    elif can_prev:
                        j = i - 1
                    else:
                        j = i + 1
                    # merge i into j
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
        """Re-split chunks longer than SPLIT_MAX with the fallback splitter."""
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
        """Split chunks that contain an explicit references heading mid-chunk."""
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
        """Mark bibliography-like chunks as references without dropping them."""
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
    # pymupdf4llm wraps every CJK/Latin token in backticks; strip them so the
    # SemanticChunker sees clean sentences and doesn't split at backtick boundaries.
    _BACKTICK_RE = _re.compile(r'`')

    def _clean_page_text(text: str) -> str:
        return _BACKTICK_RE.sub('', text)

    def _fix_orphaned_punct(text: str) -> str:
        # After pages are joined, sentence-ending punctuation that PDF line-wrapped
        # onto the next page now appears after \n\n.  Move it back to end of the
        # preceding line so the sentence splitter sees complete sentences.
        return _re.sub(r'(\S)[ \t]*\n+[ \t]*([。！？；])', r'\1\2\n', text)

    def _section_from_piece_heading(piece: str) -> str | None:
        """If the piece starts with a markdown heading, classify its section.

        For headings with explicit multi-level chapter numbering (e.g. 1-1-2, 2-3-13),
        the chapter-number hint is a strong signal and should override the page-level
        section map even when the LLM misclassified the page.
        """
        first_line = piece.lstrip('\n').split('\n')[0].strip()
        m = _MD_HEADING_RE.match(first_line)
        if not m:
            return None
        heading_text = m.group(1)
        cat = _classify_candidate_text(heading_text)
        # When the heading has explicit chapter-section numbering (e.g. "2-3-13 合成步驟")
        # and the classification came from the chapter-number heuristic, trust it
        # over whatever the page-level section map says — LLM often mislabels pages
        # that contain numbered lists as "references".
        return cat

    def _chunk_structural_piece(piece: str, base_meta: dict, piece_start: int,
                                boundaries: list, section: str) -> list:
        """Chunk one structurally-bounded piece (between heading markers)."""
        def _pages_for_pos(start: int, end: int) -> tuple[int, int]:
            pages = [pg for pg, s, e in boundaries if s < end and e > start]
            if not pages:
                return boundaries[0][0], boundaries[-1][0]
            return pages[0], pages[-1]

        # Detect section from this piece's own leading heading (overrides page-level map)
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
        search_from = 0  # byte offset in the ORIGINAL piece (not normalised)
        for chunk in splitter.split_documents([piece_doc]):
            if len(chunk.page_content.strip()) < MIN_CHUNK_CHARS:
                continue
            if _is_table_sep_only(chunk.page_content):
                continue
            # Use original text for position lookup so normalisation-induced
            # whitespace differences (e.g. in tables) don't shift offsets.
            key = chunk.page_content[:80]
            local_idx = piece.find(key, search_from)
            if local_idx == -1:
                local_idx = piece.find(key)
            if local_idx == -1:
                # Last resort: normalised lookup (SemanticChunker may have
                # collapsed whitespace differently from the source)
                piece_norm = _normalise(piece)
                key_norm = _normalise(key)
                ni = piece_norm.find(key_norm, search_from)
                if ni == -1:
                    ni = piece_norm.find(key_norm)
                local_idx = ni  # still -1 if not found
            if local_idx != -1:
                c_start = piece_start + local_idx
                c_end = c_start + len(chunk.page_content)
                search_from = local_idx + len(chunk.page_content)
            else:
                # All lookups failed — advance linearly so each chunk gets a
                # distinct estimated position rather than collapsing to one page.
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
        """Chunk one window of pages: structural boundaries first, then semantic."""
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

        # Split at markdown heading boundaries (##, ###) so chunks never cross headings
        struct_pieces = _STRUCT_SPLIT_RE.split(combined)
        if not struct_pieces:
            struct_pieces = [combined]

        # Locate each piece's start offset in combined
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
    MAX_PAGE_GAP = 5  # break window when consecutive same-section pages are far apart
    for section, s_docs in section_pages.items():
        # Slide a window, also splitting on large page gaps so that pages incorrectly
        # lumped into the same section (e.g. OCR-misclassified early chapter titles)
        # are never merged with distant content.
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

    # Final global pass: merge any small tail-chunks that slipped through
    # per-window merges (e.g. the last chunk of a section window that wasn't
    # adjacent to a same-section neighbour inside that window).
    splits = _merge_small(splits)
    splits = _dedup_overlapping(splits)
    splits = _remove_near_duplicate_chunks(splits)
    splits = _split_reference_boundaries(splits)
    splits = _retag_reference_chunks(splits)
    return splits


def extract_abstract(docs: list, lang: str) -> str | None:
    """Extract the abstract section from the loaded PDF pages.

    Searches the first 6 pages for a recognisable abstract heading, then grabs
    the text that follows until the next major section.  Falls back to the first
    substantial paragraph if no heading is found.
    """
    sample = "\n".join(d.page_content for d in docs[:6])

    # Try to find labelled abstract section
    if lang == "en":
        heading_re = re.compile(r"(?im)^(abstract|summary)\s*\n+(.+?)(?=\n{2,}[A-Z0-9]|\n{2,}[IVX]+\.|\Z)", re.DOTALL)
    else:
        heading_re = re.compile(r"(?m)^(摘要|Abstract|ABSTRACT)\s*\n+(.+?)(?=\n{2,}[一-龥A-Z0-9]|\Z)", re.DOTALL)

    m = heading_re.search(sample)
    if m:
        return m.group(2).strip()[:2000]

    # Fallback: first paragraph with ≥ 150 characters
    for doc in docs[:4]:
        for para in doc.page_content.split("\n\n"):
            para = para.strip()
            if len(para) >= 150:
                return para[:2000]
    return None


_LLAMAPARSE_CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "llamacache")
_PYMUPDF_CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pymupdfcache")

# Page separator used in all parser .md cache files.
# Must NOT be a plain "---" (markdown horizontal rule) which appears inside LlamaParse
# page content and causes false page splits when reading back the cache.
_CACHE_PAGE_SEP = "\n---PAGE---\n"

def _split_cache_pages(content: str) -> list[str]:
    """Split a .md cache file into individual page strings.

    Supports both the new unambiguous separator (_CACHE_PAGE_SEP) and the legacy
    '\\n---\\n' separator so that existing caches continue to work.
    """
    if _CACHE_PAGE_SEP in content:
        return [p.strip() for p in content.split(_CACHE_PAGE_SEP) if p.strip()]
    # Legacy format: fall back to plain ---
    return [p.strip() for p in content.split("\n---\n") if p.strip()]
_MD_HEADING_RE = re.compile(r'^#{1,3}\s+(.+)$')
_MD_INLINE_RE = re.compile(r'[`*_]')  # strip inline code/bold/italic markers for filter checks

# Headings that look like figure/table captions or mathematical context words — skip these.
# Pattern: 圖/表/Fig/Table followed immediately by a digit (e.g. "圖 2-1 …", "表 3-3 …")
_CAPTION_PREFIX_RE = re.compile(
    r'^(?:圖|表)\s*\d|^(?:Fig(?:ure)?|Table)\.?\s*\d',
    re.IGNORECASE,
)
# Single words that appear as standalone headings but are not section markers
_NON_HEADING_EXACT = frozenset({
    "其中", "其中：", "其中:",        # mathematical "where"
    "式中", "式中：",                 # "in the formula"
    "注", "注：", "備注", "說明",     # notes/remarks
})


def _parse_with_pymupdf4llm(file_path: str, force: bool = False, doc_id: int = 0) -> list:
    """Primary PDF parser: local, free, produces Markdown with # headings.
    Returns LangChain Documents tagged with pymupdf4llm=True.
    Falls back gracefully if the package is missing.
    """
    try:
        import pymupdf4llm
        from langchain_core.documents import Document as LCDocument
    except ImportError:
        logger.warning("pymupdf4llm not installed; falling back to PyMuPDFLoader")
        return []

    try:
        pages = pymupdf4llm.to_markdown(file_path, page_chunks=True)
        if not pages:
            return []
        docs = []
        for i, page_data in enumerate(pages):
            text = page_data.get("text", "") if isinstance(page_data, dict) else str(page_data)
            meta = page_data.get("metadata", {}) if isinstance(page_data, dict) else {}
            # pymupdf4llm stores page number as 'page_number' (1-indexed); convert to 0-indexed
            page_num = meta.get("page_number", i + 1) - 1
            docs.append(LCDocument(
                page_content=text,
                metadata={"page": page_num, "source": file_path, "pymupdf4llm": True},
            ))
        logger.info("pymupdf4llm: %d pages for '%s'", len(docs), os.path.basename(file_path))
        try:
            cache_path = _pymupdf_cache_path(doc_id)
            with open(cache_path, "w", encoding="utf-8") as f:
                f.write(_CACHE_PAGE_SEP.join(d.page_content for d in docs))
        except Exception:
            pass
        return docs
    except Exception as exc:
        logger.warning("pymupdf4llm failed (%s); falling back to PyMuPDFLoader", exc)
        return []


def _pymupdf_cache_path(doc_id: int) -> str:
    os.makedirs(_PYMUPDF_CACHE_DIR, exist_ok=True)
    return os.path.join(_PYMUPDF_CACHE_DIR, f"{doc_id}.md")


def _llamaparse_cache_path(doc_id: int) -> str:
    os.makedirs(_LLAMAPARSE_CACHE_DIR, exist_ok=True)
    return os.path.join(_LLAMAPARSE_CACHE_DIR, f"{doc_id}.md")


def _llamaparse_raw_cache_path(doc_id: int) -> str:
    os.makedirs(_LLAMAPARSE_CACHE_DIR, exist_ok=True)
    return os.path.join(_LLAMAPARSE_CACHE_DIR, f"{doc_id}.raw.json")


_AZURE_DI_CACHE_DIR = os.path.join(os.path.dirname(__file__), "azuredicache")


def _azure_di_cache_path(doc_id: int) -> str:
    os.makedirs(_AZURE_DI_CACHE_DIR, exist_ok=True)
    return os.path.join(_AZURE_DI_CACHE_DIR, f"{doc_id}.md")


def _html_tables_to_md(text: str) -> str:
    """Convert <table>…</table> blocks in Azure DI output to Markdown pipe tables."""
    import html as _html_mod

    def _cell_text(raw: str) -> str:
        inner = re.sub(r'<[^>]+>', ' ', raw)
        inner = _html_mod.unescape(inner)
        return re.sub(r'\s+', ' ', inner).strip().replace('|', '\\|')

    def _convert(m: re.Match) -> str:
        rows = re.findall(r'<tr[^>]*>(.*?)</tr>', m.group(0), re.DOTALL | re.IGNORECASE)
        if not rows:
            return m.group(0)
        md: list[str] = []
        for i, row_html in enumerate(rows):
            cells = re.findall(r'<(?:th|td)[^>]*>(.*?)</(?:th|td)>', row_html, re.DOTALL | re.IGNORECASE)
            if not cells:
                continue
            cleaned = [_cell_text(c) for c in cells]
            md.append('| ' + ' | '.join(cleaned) + ' |')
            if i == 0:
                md.append('|' + '|'.join(['---'] * len(cleaned)) + '|')
        return '\n'.join(md)

    return re.sub(r'<table[^>]*>.*?</table>', _convert, text, flags=re.DOTALL | re.IGNORECASE)


def _parse_with_azure_di(file_path: str, force: bool = False, doc_id: int = 0) -> list:
    """Parse PDF using Azure Document Intelligence layout model.
    Results are cached to disk. Returns LangChain Documents with azure_di=True.
    Pages with fewer than _DI_MIN_CHARS meaningful characters (CJK + alnum) are
    dropped — they are typically image-only pages, isolated figure captions, or
    short section titles that produce meaningless embedding chunks.
    """
    from langchain_core.documents import Document as LCDocument
    import re as _re

    _DI_MIN_CHARS = 30

    def _keep(text: str) -> bool:
        return sum(1 for c in text if '一' <= c <= '鿿' or c.isalnum()) >= _DI_MIN_CHARS

    cache_path = _azure_di_cache_path(doc_id)
    if not force and os.path.exists(cache_path):
        logger.info("Azure DI cache hit for '%s'", os.path.basename(file_path))
        with open(cache_path, "r", encoding="utf-8") as f:
            content = f.read()
        pages = [_html_tables_to_md(p).strip() for p in _split_cache_pages(content)]
        converted = _CACHE_PAGE_SEP.join(pages)
        if converted != content.strip():
            with open(cache_path, "w", encoding="utf-8") as f:
                f.write(converted)
        return [LCDocument(
            page_content=p,
            metadata={"page": i, "source": file_path, "azure_di": True},
        ) for i, p in enumerate(pages) if _keep(p)]

    if not settings.azure_document_intelligence_endpoint or not settings.azure_document_intelligence_key:
        logger.warning("Azure DI skipped: endpoint/key not configured")
        return []
    try:
        from azure.core.credentials import AzureKeyCredential
        from azure.ai.documentintelligence import DocumentIntelligenceClient

        client = DocumentIntelligenceClient(
            endpoint=settings.azure_document_intelligence_endpoint,
            credential=AzureKeyCredential(settings.azure_document_intelligence_key),
        )
        with open(file_path, "rb") as fh:
            poller = client.begin_analyze_document(
                "prebuilt-layout", body=fh,
                content_type="application/pdf",
                output_content_format="markdown",
                locale="zh-Hant",
                features=["ocrHighResolution", "formulas", "styleFont"],
            )
        result = poller.result()
        md = result.content or ""
        if not md:
            logger.warning("Azure DI returned empty content for '%s'", os.path.basename(file_path))
            return []

        pages = [_html_tables_to_md(p).strip() for p in _re.split(r'<!-- PageBreak -->', md) if p.strip()]
        if not pages:
            pages = [_html_tables_to_md(md).strip()]

        with open(cache_path, "w", encoding="utf-8") as f:
            f.write(_CACHE_PAGE_SEP.join(pages))

        kept = [(i, p) for i, p in enumerate(pages) if _keep(p)]
        dropped = len(pages) - len(kept)
        if dropped:
            logger.info("Azure DI dropped %d sparse page(s) for '%s'", dropped, os.path.basename(file_path))
        docs = [LCDocument(
            page_content=p,
            metadata={"page": i, "source": file_path, "azure_di": True},
        ) for i, p in kept]
        logger.info("Azure DI produced %d pages for '%s' (cached)", len(docs), os.path.basename(file_path))
        return docs
    except Exception as e:
        logger.error("Azure DI failed: %s", e)
        return []


def _extract_headings_from_markdown(docs: list) -> list[dict]:
    """Extract Markdown # headings from LlamaParse / pymupdf4llm output.

    Filters out figure/table captions (圖 X-X, 表 X-X, Fig. N, Table N),
    mathematical context words (其中, 式中), and parenthesised annotations.
    Repeated identical texts (≥3 occurrences) are table-column headers, not headings.
    """
    raw: list[dict] = []
    plain_counts: dict[str, int] = {}
    for doc in docs:
        page = doc.metadata.get("page", 0)
        for line in doc.page_content.splitlines():
            m = _MD_HEADING_RE.match(line.strip())
            if not m:
                continue
            text = m.group(1).strip()
            if not text or len(text) > 60:
                continue
            if '，' in text:
                continue
            # Strip inline markdown (backticks, bold/italic) for filter checks;
            # pymupdf4llm wraps tokens in backticks e.g. `圖` 2-1 `caption`
            plain = _MD_INLINE_RE.sub('', text).strip()
            # Skip figure / table captions
            if _CAPTION_PREFIX_RE.match(plain):
                continue
            # Skip non-heading exact matches
            if plain in _NON_HEADING_EXACT:
                continue
            # Skip parenthesised annotations like "(單位:mm)" — but allow numbered
            # section headings like "( 四 ) 研究方法" or "（一）研究背景"
            if plain.startswith(('(', '（', '〔', '[')):
                if not re.match(
                    r'^[（(〔\[]\s*[一二三四五六七八九十壹貳參肆伍陸柒捌玖拾\d]',
                    plain,
                ):
                    continue
            raw.append({"page": page, "text": text, "_plain": plain})
            plain_counts[plain] = plain_counts.get(plain, 0) + 1

    # Filter out repeated labels (≥3 identical plain texts) — these are table-column headers
    return [{"page": h["page"], "text": h["text"]} for h in raw if plain_counts[h["_plain"]] < 3]


def _strip_code_fence(text: str) -> str:
    """Remove ```markdown / ``` wrappers that LlamaParse adds around each page."""
    t = text.strip()
    t = re.sub(r'^```(?:markdown)?\s*\n?', '', t)
    t = re.sub(r'\n?```\s*$', '', t)
    return t.strip()


def _page_text_from_payload(page: dict) -> str:
    """Extract text from a LlamaParse page payload dict."""
    candidates = (
        page.get("md"),
        page.get("markdown"),
        page.get("text"),
        page.get("content"),
        page.get("raw_text"),
    )
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip() and candidate.strip() != "NO_CONTENT_HERE":
            return _strip_code_fence(candidate)
    return ""


def _parse_with_llamaparse(file_path: str, force: bool = False, doc_id: int = 0) -> list:
    """Fallback PDF parser using LlamaParse (cloud Vision AI).
    Results are cached to disk to avoid repeated API calls.
    Returns LangChain-compatible Document objects with metadata llamaparse=True.
    """
    from langchain_core.documents import Document as LCDocument

    def _llamaparse_get_json_result(path: str, target_pages: list[int] | None = None) -> list[dict]:
        import httpx

        base_url = os.getenv("LLAMA_CLOUD_BASE_URL", "https://api.cloud.llamaindex.ai").rstrip("/")
        upload_url = f"{base_url}/api/parsing/upload"
        status_url = f"{base_url}/api/parsing/job/{{job_id}}"
        result_url = f"{base_url}/api/parsing/job/{{job_id}}/result/json"
        headers = {"Authorization": f"Bearer {settings.llama_cloud_api_key}"}
        def _build_request_data() -> dict[str, str]:
            data: dict[str, str] = {
                "result_type": "markdown",
                "language": "ch_tra",
                "high_res_ocr": "true",
                "preserve_very_small_text": "true",
                "skip_diagonal_text": "true",
                "adaptive_long_table": "true",
                "outlined_table_extraction": "true",
                "merge_tables_across_pages_in_markdown": "true",
                "hide_headers": "true",
                "hide_footers": "true",
                "replace_failed_page_mode": "raw_text",
                "page_error_tolerance": "1.0",
                "invalidate_cache": "true",
            }
            if target_pages is not None:
                data["target_pages"] = ",".join(str(p) for p in target_pages)
            data.update({
                "system_prompt_append": (
                    "This is a Traditional Chinese academic research report. "
                    "Preserve all mathematical formulas using LaTeX ($...$). "
                    "Preserve table structure. Output all Chinese text accurately. "
                    "For pages that are primarily figures or images, output any visible "
                    "figure caption or label using <figcaption> tags, e.g. "
                    "<figcaption>圖4-7 阪神大地震之地表加速度歷時圖</figcaption>. "
                    "If no caption is visible, output a brief description of what the figure shows."
                ),
            })
            no_azure = os.getenv("LLAMAPARSE_NO_AZURE", "").strip() in {"1", "true", "yes"}
            data["parse_mode"] = "parse_page_with_lvm"
            if not no_azure and settings.azure_openai_endpoint and settings.azure_openai_api_key and settings.azure_chat_deployment:
                base = settings.azure_openai_endpoint.rstrip("/")
                full_endpoint = (
                    f"{base}/openai/deployments/{settings.azure_chat_deployment}"
                    f"/chat/completions?api-version=2024-08-01-preview"
                )
                data["vendor_multimodal_model_name"] = "custom-azure-model"
                data["azure_openai_endpoint"] = full_endpoint
                data["azure_openai_key"] = settings.azure_openai_api_key
                data["azure_openai_api_version"] = "2024-08-01-preview"
                data["azure_openai_deployment_name"] = settings.azure_chat_deployment
                logger.info(
                    "LlamaParse using Azure OpenAI (custom-azure-model) deployment '%s'",
                    settings.azure_chat_deployment,
                )
            else:
                data["vendor_multimodal_model_name"] = "openai-gpt4o"
                if no_azure:
                    logger.info("LlamaParse: LLAMAPARSE_NO_AZURE=1, using LlamaParse's own model")
            return data

        def _run_job(client: httpx.Client, path: str, data: dict[str, str]) -> list[dict]:
            with open(path, "rb") as fh:
                files = {"file": (os.path.basename(path), fh, "application/pdf")}
                upload_resp = client.post(upload_url, data=data, files=files)
            if not upload_resp.is_success:
                logger.error("LlamaParse upload failed %d: %s", upload_resp.status_code, upload_resp.text[:500])
            upload_resp.raise_for_status()
            job_id = upload_resp.json()["id"]
            logger.info("LlamaParse upload created job %s for '%s'", job_id, os.path.basename(path))

            start = time.time()
            poll_interval = 2.0
            while True:
                status_resp = client.get(status_url.format(job_id=job_id))
                status_resp.raise_for_status()
                status_json = status_resp.json()
                status = status_json.get("status")
                if status == "SUCCESS":
                    result_resp = client.get(result_url.format(job_id=job_id))
                    result_resp.raise_for_status()
                    result_json = result_resp.json()
                    return result_json if isinstance(result_json, list) else [result_json]
                if status in {"ERROR", "CANCELED"}:
                    page_errors = status_json.get("page_errors") or status_json.get("pages_errors") or []
                    if page_errors:
                        for pe in page_errors[:10]:
                            logger.warning("LlamaParse page error: %s", pe)
                    else:
                        logger.warning("LlamaParse full status: %s", status_json)
                    raise RuntimeError(
                        f"LlamaParse job {job_id} failed with status={status} "
                        f"error_code={status_json.get('error_code')} "
                        f"error_message={status_json.get('error_message')}"
                    )
                if time.time() - start > 1800:
                    raise TimeoutError(f"LlamaParse job {job_id} timed out after 1800 seconds")
                time.sleep(poll_interval)

        timeout = httpx.Timeout(120.0, connect=30.0, read=120.0, write=120.0)
        with httpx.Client(timeout=timeout, headers=headers) as client:
            while True:
                try:
                    return _run_job(client, path, _build_request_data())
                except RuntimeError as e:
                    if "error_code=MULTIMODAL_ERROR" not in str(e):
                        raise
                    logger.warning(
                        "LlamaParse MULTIMODAL_ERROR for '%s'; retrying once",
                        os.path.basename(path),
                    )
                    try:
                        return _run_job(client, path, _build_request_data())
                    except RuntimeError as e2:
                        if "error_code=MULTIMODAL_ERROR" in str(e2):
                            logger.warning("LlamaParse retry also hit MULTIMODAL_ERROR for '%s'; raising", os.path.basename(path))
                        raise

    cache_path = _llamaparse_cache_path(doc_id)
    raw_cache_path = _llamaparse_raw_cache_path(doc_id)
    # backward compat: old .json cache
    json_cache = cache_path[:-3] + ".json"
    if not force and os.path.exists(cache_path):
        logger.info("LlamaParse cache hit for '%s'", os.path.basename(file_path))
        with open(cache_path, "r", encoding="utf-8") as f:
            content = f.read()
        pages = [_strip_code_fence(p) for p in _split_cache_pages(content)]
        pages = [p for p in pages if p]
        # Rewrite cache if any page had code fences stripped
        cleaned = _CACHE_PAGE_SEP.join(pages)
        if cleaned != content.strip():
            with open(cache_path, "w", encoding="utf-8") as f:
                f.write(cleaned)
            logger.info("LlamaParse cache rewritten (stripped code fences) for '%s'", os.path.basename(file_path))
        return [LCDocument(
            page_content=p,
            metadata={"page": i, "source": file_path, "llamaparse": True},
        ) for i, p in enumerate(pages)]
    if not force and os.path.exists(json_cache):
        logger.info("LlamaParse json cache hit for '%s'", os.path.basename(file_path))
        with open(json_cache, "r", encoding="utf-8") as f:
            cached = json.load(f)
        docs = []
        for i, item in enumerate(cached):
            if not isinstance(item, dict):
                continue
            text = _page_text_from_payload(item)
            if not text:
                logger.warning(
                    "LlamaParse legacy json cache entry missing text-like fields for '%s' at index %d; keys=%s",
                    os.path.basename(file_path),
                    i,
                    sorted(item.keys()),
                )
                continue
            docs.append(LCDocument(
                page_content=text,
                metadata={
                    "page": item.get("page", i),
                    "source": file_path,
                    "llamaparse": True,
                },
            ))
        if docs:
            return docs
        logger.warning("LlamaParse legacy json cache unusable for '%s'", os.path.basename(file_path))

    if not settings.llama_cloud_api_key:
        logger.warning("LlamaParse skipped: LLAMA_CLOUD_API_KEY not set")
        return []
    try:
        logger.info("LlamaParse: parse_page_with_lvm mode via raw REST API")
        json_results = _llamaparse_get_json_result(file_path)
        if not json_results:
            return []

        pages = []
        for result_idx, result in enumerate(json_results):
            if not isinstance(result, dict):
                logger.warning(
                    "LlamaParse returned non-dict result for '%s' at index %d: %s",
                    os.path.basename(file_path),
                    result_idx,
                    type(result).__name__,
                )
                continue

            result_pages = result.get("pages", [])
            if not isinstance(result_pages, list):
                logger.warning(
                    "LlamaParse result missing pages list for '%s' at index %d; keys=%s",
                    os.path.basename(file_path),
                    result_idx,
                    sorted(result.keys()),
                )
                continue

            for page_idx, page in enumerate(result_pages):
                if not isinstance(page, dict):
                    continue
                # LlamaParse page numbers are 1-indexed; always use the loop counter
                # (0-indexed) so page metadata stays aligned with the cache which is
                # also stored and loaded as sequential 0-indexed positions.
                actual_page = page_idx
                text = _page_text_from_payload(page)
                if not text:
                    # Distinguish genuine extraction failures from image-only pages.
                    # noTextContent=False + status=WARNING means the vision model knew
                    # there was text but failed to extract it (API overload / timeout).
                    # Mark these as [0 x 0] ghosts so the pymupdf4llm fallback handles them.
                    is_extraction_failure = (
                        page.get("noTextContent") is False
                        and page.get("status") == "WARNING"
                    )
                    if is_extraction_failure:
                        placeholder = "**==> picture [0 x 0] intentionally omitted <==**"
                    else:
                        images = page.get("images") or []
                        if images:
                            parts = []
                            for img in images[:3]:
                                w, h = img.get("width", "?"), img.get("height", "?")
                                parts.append(f"**==> picture [{w} x {h}] intentionally omitted <==**")
                            placeholder = "\n".join(parts)
                        else:
                            placeholder = "**==> picture intentionally omitted <==**"
                    pages.append((actual_page, placeholder, False))
                else:
                    pages.append((actual_page, text, True))
        if not pages:
            logger.warning(
                "LlamaParse returned no usable page text for '%s'; raw response saved to %s",
                os.path.basename(file_path),
                raw_cache_path,
            )
            return []

        # Build a position map: llama 1-indexed page → (result_idx, page_idx_in_result)
        # Used to merge retry results back into json_results before saving raw.json.
        _raw_pos: dict[int, tuple[int, int]] = {}
        for r_idx, result in enumerate(json_results):
            for p_idx, page in enumerate(result.get("pages", [])):
                if isinstance(page, dict):
                    _raw_pos[page.get("page", p_idx + 1)] = (r_idx, p_idx)

        # Ghost = [0 x 0] placeholder → LlamaParse vision failed (not truly blank).
        # Retry these pages up to 3 times before giving up.
        pages_mut = [list(p) for p in pages]  # make mutable: [page_num, text, has_content]
        remaining_ghosts = {
            row[0] for row in pages_mut if not row[2] and "[0 x 0]" in row[1]
        }
        if remaining_ghosts:
            logger.warning(
                "LlamaParse: %d ghost [0x0] page(s) for '%s' — retrying (up to 3×)",
                len(remaining_ghosts), os.path.basename(file_path),
            )
            for attempt in range(3):
                if not remaining_ghosts:
                    break
                target_0idx = sorted(remaining_ghosts)
                logger.info("Ghost retry %d/3: pages %s", attempt + 1, target_0idx)
                try:
                    retry_results = _llamaparse_get_json_result(file_path, target_pages=target_0idx)
                    new_pgs: dict[int, dict] = {}  # llama 1-indexed → page dict
                    for rr in (retry_results if isinstance(retry_results, list) else [retry_results]):
                        for pg in rr.get("pages", []):
                            if isinstance(pg, dict) and pg.get("page") is not None:
                                new_pgs[pg["page"]] = pg
                    recovered = set()
                    for row in pages_mut:
                        actual_pn = row[0]
                        if actual_pn not in remaining_ghosts:
                            continue
                        llama_pg = actual_pn + 1  # 0-indexed → 1-indexed
                        new_pg = new_pgs.get(llama_pg)
                        if new_pg is None:
                            continue
                        new_text = _page_text_from_payload(new_pg)
                        if new_text:
                            row[1] = new_text
                            row[2] = True
                            recovered.add(actual_pn)
                            # Merge back into json_results
                            pos = _raw_pos.get(llama_pg)
                            if pos:
                                json_results[pos[0]]["pages"][pos[1]] = new_pg
                            logger.info("Ghost page %d recovered (attempt %d, %d chars)",
                                        actual_pn, attempt + 1, len(new_text))
                    remaining_ghosts -= recovered
                    logger.info("Ghost retry %d/3 done: recovered=%d still_ghost=%d",
                                attempt + 1, len(recovered), len(remaining_ghosts))
                except Exception as _ge:
                    logger.warning("Ghost page retry %d/3 failed: %s", attempt + 1, _ge)
            pages = [(row[0], row[1], row[2]) for row in pages_mut]
            if remaining_ghosts:
                logger.warning(
                    "Ghost pages still empty after 3 LlamaParse retries: %s — falling back to pymupdf4llm",
                    sorted(remaining_ghosts),
                )
                try:
                    pymupdf_docs = _parse_with_pymupdf4llm(file_path, doc_id=doc_id)
                    pymupdf_map = {d.metadata.get("page", 0): d.page_content for d in pymupdf_docs}
                    pages = [
                        (pn, pymupdf_map[pn].strip(), True)
                        if pn in remaining_ghosts and pymupdf_map.get(pn, "").strip()
                        else (pn, text, hc)
                        for pn, text, hc in pages
                    ]
                    logger.info("pymupdf4llm filled %d/%d remaining ghost pages",
                                sum(1 for pn in remaining_ghosts if pymupdf_map.get(pn, "").strip()),
                                len(remaining_ghosts))
                except Exception as _fe:
                    logger.warning("pymupdf4llm fallback after retries failed: %s", _fe)

        # Save raw.json (after retries so recovered pages are included)
        with open(raw_cache_path, "w", encoding="utf-8") as f:
            json.dump(json_results, f, ensure_ascii=False, indent=2)

        with open(cache_path, "w", encoding="utf-8") as f:
            f.write(_CACHE_PAGE_SEP.join(text for _, text, _ in pages))

        docs = [LCDocument(
            page_content=text,
            metadata={"page": page_num, "source": file_path, "llamaparse": True},
        ) for page_num, text, has_content in pages if has_content]
        image_pages = sum(1 for _, _, has_content in pages if not has_content)
        logger.info(
            "LlamaParse produced %d text pages + %d image-only pages for '%s' (cached)",
            len(docs), image_pages, os.path.basename(file_path),
        )
        return docs
    except Exception as e:
        logger.error(
            "LlamaParse failed for '%s': %s",
            os.path.basename(file_path),
            e,
        )
        return []


def retry_llamaparse_warning_pages(file_path: str, doc_id: int) -> dict:
    """Re-submit only the WARNING / NO_CONTENT_HERE pages to LlamaParse.

    Reads the existing raw.json, identifies pages where status='WARNING' and
    noTextContent=False (i.e. the vision model failed silently), re-submits
    just those page indices via the `target_pages` API parameter, merges the
    new results back into raw.json, and rebuilds the .md cache.

    Returns:
        {
            "retried":  list of 0-indexed page positions that were re-submitted,
            "recovered": int — pages that now have real text after retry,
            "still_failed": int — pages that are still empty after retry,
        }
    """
    raw_cache_path = _llamaparse_raw_cache_path(doc_id)
    cache_path = _llamaparse_cache_path(doc_id)

    if not os.path.exists(raw_cache_path):
        raise FileNotFoundError(f"No raw.json cache for doc_id={doc_id}: {raw_cache_path}")
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"PDF not found: {file_path}")
    if not settings.llama_cloud_api_key:
        raise RuntimeError("LLAMA_CLOUD_API_KEY not set")

    with open(raw_cache_path, "r", encoding="utf-8") as f:
        raw_data = json.load(f)

    results = raw_data if isinstance(raw_data, list) else [raw_data]

    # Collect WARNING pages: {llama_1indexed_page: (result_idx, page_idx_in_result)}
    warning_map: dict[int, tuple[int, int]] = {}
    for r_idx, result in enumerate(results):
        for p_idx, page in enumerate(result.get("pages", [])):
            if (
                page.get("noTextContent") is False
                and page.get("status") == "WARNING"
            ):
                llama_page = page.get("page", p_idx + 1)  # 1-indexed
                warning_map[llama_page] = (r_idx, p_idx)

    if not warning_map:
        logger.info("No WARNING pages found in raw.json for doc_id=%d", doc_id)
        return {"retried": [], "recovered": 0, "still_failed": 0}

    # Convert to 0-indexed for target_pages parameter
    all_target_0idx = sorted(p - 1 for p in warning_map.keys())
    logger.info(
        "Retrying %d WARNING page(s) for doc_id=%d: 0-indexed=%s",
        len(all_target_0idx), doc_id, all_target_0idx,
    )

    import httpx, time as _time

    base_url = os.getenv("LLAMA_CLOUD_BASE_URL", "https://api.cloud.llamaindex.ai").rstrip("/")
    upload_url = f"{base_url}/api/parsing/upload"
    status_url = f"{base_url}/api/parsing/job/{{job_id}}"
    result_url = f"{base_url}/api/parsing/job/{{job_id}}/result/json"
    headers = {"Authorization": f"Bearer {settings.llama_cloud_api_key}"}
    no_azure = os.getenv("LLAMAPARSE_NO_AZURE", "").strip() in {"1", "true", "yes"}

    def _build_retry_data(target_0idx: list[int]) -> dict[str, str]:
        d: dict[str, str] = {
            "result_type": "markdown",
            "language": "ch_tra",
            "high_res_ocr": "true",
            "preserve_very_small_text": "true",
            "adaptive_long_table": "true",
            "outlined_table_extraction": "true",
            "replace_failed_page_mode": "raw_text",
            "page_error_tolerance": "1.0",
            "invalidate_cache": "true",
            "target_pages": ",".join(str(p) for p in target_0idx),
            "parse_mode": "parse_page_with_lvm",
        }
        if not no_azure and settings.azure_openai_endpoint and settings.azure_openai_api_key and settings.azure_chat_deployment:
            base = settings.azure_openai_endpoint.rstrip("/")
            d["vendor_multimodal_model_name"] = "custom-azure-model"
            d["azure_openai_endpoint"] = (
                f"{base}/openai/deployments/{settings.azure_chat_deployment}"
                f"/chat/completions?api-version=2024-08-01-preview"
            )
            d["azure_openai_key"] = settings.azure_openai_api_key
            d["azure_openai_api_version"] = "2024-08-01-preview"
            d["azure_openai_deployment_name"] = settings.azure_chat_deployment
        else:
            d["vendor_multimodal_model_name"] = "openai-gpt4o"
        return d

    def _run_retry_job(client: httpx.Client, target_0idx: list[int]) -> dict[int, dict]:
        """Send target_pages to LlamaParse, return {llama_1indexed_page: page_dict}."""
        with open(file_path, "rb") as fh:
            files = {"file": (os.path.basename(file_path), fh, "application/pdf")}
            resp = client.post(upload_url, data=_build_retry_data(target_0idx), files=files)
        resp.raise_for_status()
        job_id = resp.json()["id"]
        logger.info("LlamaParse retry job %s for doc_id=%d pages=%s", job_id, doc_id, target_0idx)
        start = _time.time()
        while True:
            sr = client.get(status_url.format(job_id=job_id))
            sr.raise_for_status()
            sj = sr.json()
            st = sj.get("status")
            if st == "SUCCESS":
                rr = client.get(result_url.format(job_id=job_id))
                rr.raise_for_status()
                new_results = rr.json()
                new_results = new_results if isinstance(new_results, list) else [new_results]
                out: dict[int, dict] = {}
                for nr in new_results:
                    for pg in nr.get("pages", []):
                        if isinstance(pg, dict) and pg.get("page") is not None:
                            out[pg["page"]] = pg
                return out
            if st in {"ERROR", "CANCELED"}:
                raise RuntimeError(f"LlamaParse retry job {job_id} failed: {st}")
            if _time.time() - start > 600:
                raise TimeoutError(f"LlamaParse retry job {job_id} timed out")
            _time.sleep(3)

    # Retry loop — up to 3 attempts, each time only sending still-failing pages
    remaining = set(warning_map.keys())  # llama 1-indexed
    recovered = 0

    timeout = httpx.Timeout(120.0, connect=30.0, read=120.0, write=120.0)
    with httpx.Client(timeout=timeout, headers=headers) as client:
        for attempt in range(3):
            if not remaining:
                break
            target_0idx = sorted(p - 1 for p in remaining)
            logger.info("WARNING retry %d/3 for doc_id=%d: %s", attempt + 1, doc_id, target_0idx)
            try:
                new_pgs = _run_retry_job(client, target_0idx)
                newly_recovered = set()
                for llama_page in list(remaining):
                    new_pg = new_pgs.get(llama_page)
                    if new_pg is None:
                        continue
                    new_text = _page_text_from_payload(new_pg)
                    if new_text:
                        r_idx, p_idx = warning_map[llama_page]
                        results[r_idx]["pages"][p_idx] = new_pg
                        newly_recovered.add(llama_page)
                        logger.info("Page %d recovered (attempt %d, %d chars)",
                                    llama_page, attempt + 1, len(new_text))
                    else:
                        # Still empty — update raw so status is current
                        r_idx, p_idx = warning_map[llama_page]
                        results[r_idx]["pages"][p_idx] = new_pg
                recovered += len(newly_recovered)
                remaining -= newly_recovered
                logger.info("WARNING retry %d/3 done: recovered=%d still=%d",
                            attempt + 1, len(newly_recovered), len(remaining))
            except Exception as _e:
                logger.warning("WARNING retry %d/3 failed: %s", attempt + 1, _e)

    still_failed = len(remaining)
    if still_failed:
        logger.warning("Pages still empty after 3 retries for doc_id=%d: %s — likely truly blank",
                       doc_id, sorted(remaining))

    # Save updated raw.json
    with open(raw_cache_path, "w", encoding="utf-8") as f:
        json.dump(results if isinstance(raw_data, list) else results[0], f, ensure_ascii=False, indent=2)
    logger.info("Updated raw.json saved for doc_id=%d (recovered=%d still_failed=%d)",
                doc_id, recovered, still_failed)

    # Immediately rebuild .md cache from the updated raw.json
    rebuild_llamaparse_md_from_raw(file_path, doc_id)

    return {
        "retried": all_target_0idx,
        "recovered": recovered,
        "still_failed": still_failed,
    }


def rebuild_llamaparse_md_from_raw(file_path: str, doc_id: int) -> list:
    """Rebuild the LlamaParse .md cache from an existing raw.json without calling the API.

    Reads raw.json, applies the same page-processing logic as the live API path
    (ghost-page detection, pymupdf4llm fallback for [0 x 0] pages), and writes a
    fresh .md cache.  Call this after raw.json has been patched (e.g. by
    retry_llamaparse_warning_pages) to get an up-to-date cache without a full reparse.

    Returns the list of LCDocument pages (same as _parse_with_llamaparse would).
    Raises FileNotFoundError if raw.json does not exist.
    """
    from langchain_core.documents import Document as LCDocument

    raw_cache_path = _llamaparse_raw_cache_path(doc_id)
    cache_path = _llamaparse_cache_path(doc_id)

    if not os.path.exists(raw_cache_path):
        raise FileNotFoundError(f"No raw.json cache for doc_id={doc_id}: {raw_cache_path}")

    with open(raw_cache_path, "r", encoding="utf-8") as f:
        raw_data = json.load(f)

    json_results = raw_data if isinstance(raw_data, list) else [raw_data]

    pages: list[tuple[int, str, bool]] = []
    for result_idx, result in enumerate(json_results):
        if not isinstance(result, dict):
            continue
        result_pages = result.get("pages", [])
        if not isinstance(result_pages, list):
            continue
        for page_idx, page in enumerate(result_pages):
            if not isinstance(page, dict):
                continue
            actual_page = page_idx
            text = _page_text_from_payload(page)
            if not text:
                is_extraction_failure = (
                    page.get("noTextContent") is False
                    and page.get("status") == "WARNING"
                )
                if is_extraction_failure:
                    placeholder = "**==> picture [0 x 0] intentionally omitted <==**"
                else:
                    images = page.get("images") or []
                    if images:
                        parts = []
                        for img in images[:3]:
                            w, h = img.get("width", "?"), img.get("height", "?")
                            parts.append(f"**==> picture [{w} x {h}] intentionally omitted <==**")
                        placeholder = "\n".join(parts)
                    else:
                        placeholder = "**==> picture intentionally omitted <==**"
                pages.append((actual_page, placeholder, False))
            else:
                pages.append((actual_page, text, True))

    if not pages:
        logger.warning("rebuild_llamaparse_md_from_raw: raw.json has no usable pages for doc_id=%d", doc_id)
        return []

    # Write initial cache
    with open(cache_path, "w", encoding="utf-8") as f:
        f.write(_CACHE_PAGE_SEP.join(text for _, text, _ in pages))

    # Substitute [0 x 0] ghost pages with pymupdf4llm fallback
    ghost_indices = {
        page_num
        for page_num, text, has_content in pages
        if not has_content and "[0 x 0]" in text
    }
    if ghost_indices:
        logger.warning(
            "rebuild_llamaparse_md_from_raw: %d ghost page(s) after retries for doc_id=%d — falling back to pymupdf4llm",
            len(ghost_indices), doc_id,
        )
        try:
            pymupdf_docs = _parse_with_pymupdf4llm(file_path, doc_id=doc_id)
            pymupdf_map = {d.metadata.get("page", 0): d.page_content for d in pymupdf_docs}
            pages = [
                (pn, pymupdf_map[pn].strip(), True)
                if pn in ghost_indices and pymupdf_map.get(pn, "").strip()
                else (pn, text, hc)
                for pn, text, hc in pages
            ]
            with open(cache_path, "w", encoding="utf-8") as f:
                f.write(_CACHE_PAGE_SEP.join(t for _, t, _ in pages))
        except Exception as _fe:
            logger.warning("pymupdf4llm fallback failed during md rebuild: %s", _fe)

    docs = [
        LCDocument(
            page_content=text,
            metadata={"page": page_num, "source": file_path, "llamaparse": True},
        )
        for page_num, text, has_content in pages
        if has_content
    ]
    image_pages = sum(1 for _, _, has_content in pages if not has_content)
    logger.info(
        "rebuild_llamaparse_md_from_raw: wrote %d text pages + %d image pages to cache for doc_id=%d",
        len(docs), image_pages, doc_id,
    )
    return docs


def _read_from_cache(file_path: str, parser: str, doc_id: int = 0) -> list:
    """Read parsed pages from a cache file only — never calls any API.
    Returns [] if the cache does not exist.
    """
    from langchain_core.documents import Document as LCDocument
    cache_fn = {
        "pymupdf4llm": _pymupdf_cache_path,
        "azure_di":    _azure_di_cache_path,
        "llamaparse":  _llamaparse_cache_path,
    }.get(parser)
    if not cache_fn:
        return []
    cache_path = cache_fn(doc_id)
    if not os.path.exists(cache_path):
        return []
    with open(cache_path, "r", encoding="utf-8") as f:
        content = f.read()
    tag = {parser: True}
    return [
        LCDocument(page_content=p, metadata={"page": i, "source": file_path, **tag})
        for i, p in enumerate(_split_cache_pages(content))
        if p.strip()
    ]


def validate_llamaparse_vs_pymupdf(
    llamaparse_pages: list,
    pymupdf_pages: list,
    *,
    sparse_threshold: int = 80,
    rich_threshold: int = 150,
) -> list[dict]:
    """Compare LlamaParse and pymupdf4llm page-by-page and return suspicious pages.

    A page is flagged as suspicious when pymupdf returned substantial text
    (> rich_threshold chars) but LlamaParse returned very little
    (< sparse_threshold chars).  This pattern indicates that LlamaParse's
    vision model failed to extract text from the page — often because the API
    was under load or the page-level vision call timed out silently.

    Returns a list of dicts:
        {"page": 1-indexed, "llama_chars": N, "pymupdf_chars": N,
         "llama_preview": "...", "severity": "warn"|"error"}
    """
    # Build page-index → content maps (0-indexed)
    def _page_map(docs: list) -> dict[int, str]:
        m: dict[int, str] = {}
        for d in docs:
            pg = d.metadata.get("page", 0)
            m[pg] = d.page_content
        return m

    llama_map = _page_map(llamaparse_pages)
    pymupdf_map = _page_map(pymupdf_pages)

    issues = []
    for pg, pymupdf_text in pymupdf_map.items():
        pymupdf_chars = len(pymupdf_text.strip())
        if pymupdf_chars < rich_threshold:
            continue  # pymupdf itself has little — skip (might be genuine image page)
        llama_text = llama_map.get(pg, "")
        llama_chars = len(llama_text.strip())
        if llama_chars >= sparse_threshold:
            continue  # llamaparse has enough content — fine
        # LlamaParse is sparse on a page where pymupdf has text → suspicious
        severity = "error" if llama_chars < 30 else "warn"
        issues.append({
            "page": pg + 1,          # 1-indexed for display
            "llama_chars": llama_chars,
            "pymupdf_chars": pymupdf_chars,
            "llama_preview": llama_text.strip()[:120],
            "pymupdf_preview": pymupdf_text.strip()[:120],
            "severity": severity,
        })
    issues.sort(key=lambda x: x["page"])
    return issues


def parse_pdf_to_cache(file_path: str, parser: str, doc_id: int = 0) -> dict:
    """Explicitly run one parser and (re)write its cache.
    Returns {"parser", "pages", "chars", "quality_issue"}.
    """
    if parser == "pymupdf4llm":
        docs = _parse_with_pymupdf4llm(file_path, force=True, doc_id=doc_id)
        if not docs:
            raw = PyMuPDFLoader(file_path).load()
            cache_path = _pymupdf_cache_path(doc_id)
            os.makedirs(os.path.dirname(cache_path), exist_ok=True)
            with open(cache_path, "w", encoding="utf-8") as f:
                f.write(_CACHE_PAGE_SEP.join(d.page_content for d in raw))
            docs = raw
    elif parser == "azure_di":
        docs = _parse_with_azure_di(file_path, force=True, doc_id=doc_id)
    elif parser == "llamaparse":
        docs = _parse_with_llamaparse(file_path, force=True, doc_id=doc_id)
    else:
        raise ValueError(f"Unknown parser: {parser}")

    lang = _detect_language(docs)
    for d in docs:
        d.page_content = _clean_text(d.page_content, lang)
    quality_issue = _check_quality(docs)
    return {
        "parser": parser,
        "pages": len(docs),
        "chars": sum(len(d.page_content) for d in docs),
        "quality_issue": quality_issue,
    }


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
        # ── Specific parser: always read from cache when cache_only=True ──────
        if cache_only:
            docs = _read_from_cache(file_path, parser, document_id)
            if not docs:
                raise ValueError(
                    f"沒有 {parser} 的快取，請先在「解析對比」中執行解析再嵌入"
                )
            parser_used = parser
        else:
            # upload/first-time path: call parser (which auto-caches)
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
        # ── Auto mode ─────────────────────────────────────────────────────────
        # Cache priority: llamaparse > azure_di > pymupdf4llm (local).
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

    # Extra check for pymupdf4llm: even if English content passes the ratio test,
    # Chinese font encoding may still be broken. PyMuPDF replaces undecodable glyphs
    # with U+FFFD (replacement character). If >25% of heading chars are U+FFFD → garbled.
    if quality_issue is None and any(d.metadata.get("pymupdf4llm") for d in docs):
        md_headings = _extract_headings_from_markdown(docs)
        if len(md_headings) >= 3:
            heading_text = " ".join(h["text"] for h in md_headings)
            total = sum(1 for c in heading_text if not c.isspace())
            if total > 0:
                replacement = heading_text.count('\ufffd')
                if replacement / total > 0.25:
                    quality_issue = "garbled"
                    logger.info("pymupdf4llm heading garble detected for '%s' (fffd=%.0f%%)",
                                os.path.basename(file_path), replacement / total * 100)

    # auto mode fallback: upgrade garbled/scanned to cloud parser.
    # cache_only → only use existing caches; otherwise call API.
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

    # Sort by page before assigning chunk_index so the index always reflects
    # document reading order, regardless of which section each chunk belongs to.
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


def delete_document_vectors(document_id: int) -> None:
    """Delete all vectors for a given document from Qdrant."""
    get_qdrant_client().delete(
        collection_name=COLLECTION_NAME,
        points_selector=Filter(
            must=[FieldCondition(
                key="metadata.document_id",
                match=MatchValue(value=str(document_id)),
            )]
        ),
    )


def delete_old_document_vectors(document_id: int, keep_ts: int) -> None:
    """Delete all vectors for document EXCEPT those with keep_ts (called after safe reindex succeeds)."""
    get_qdrant_client().delete(
        collection_name=COLLECTION_NAME,
        points_selector=Filter(
            must=[FieldCondition(key="metadata.document_id", match=MatchValue(value=str(document_id)))],
            must_not=[FieldCondition(key="metadata.reindex_ts", match=MatchValue(value=keep_ts))],
        ),
    )


def delete_pending_document_vectors(document_id: int, ts: int) -> None:
    """Delete only vectors with the given reindex_ts (cleanup when reindex fails partway)."""
    get_qdrant_client().delete(
        collection_name=COLLECTION_NAME,
        points_selector=Filter(
            must=[
                FieldCondition(key="metadata.document_id", match=MatchValue(value=str(document_id))),
                FieldCondition(key="metadata.reindex_ts", match=MatchValue(value=ts)),
            ]
        ),
    )


def update_document_vector_filename(document_id: int, filename: str) -> int:
    """Update filename metadata for all Qdrant points belonging to a document."""
    client = get_qdrant_client()
    updated = 0
    offset = None
    while True:
        points, offset = client.scroll(
            collection_name=COLLECTION_NAME,
            scroll_filter=Filter(
                must=[FieldCondition(
                    key="metadata.document_id",
                    match=MatchValue(value=str(document_id)),
                )]
            ),
            limit=256,
            with_payload=True,
            with_vectors=False,
            offset=offset,
        )
        if not points:
            break
        for point in points:
            payload = point.payload or {}
            metadata = dict(payload.get("metadata") or {})
            metadata["filename"] = filename
            client.set_payload(
                collection_name=COLLECTION_NAME,
                payload={"metadata": metadata},
                points=[point.id],
            )
            updated += 1
        if offset is None:
            break
    return updated


# ── Retrieval ─────────────────────────────────────────────────────────────────

class RetrievedChunk(TypedDict):
    filename: str
    page: int | str
    page_end: int | str     # last page of the chunk (may equal page for single-page chunks)
    section: str
    content: str
    is_low_quality: bool   # True = table/formula fragment; agent should re-search


def count_document_chunks(document_ids: list[int] | None) -> int:
    """Return the total number of stored chunks for the given documents."""
    client = get_qdrant_client()
    qdrant_filter = None
    if document_ids:
        qdrant_filter = Filter(
            must=[FieldCondition(
                key="metadata.document_id",
                match=MatchAny(any=[str(did) for did in document_ids]),
            )]
        )
    result = client.count(
        collection_name=COLLECTION_NAME,
        count_filter=qdrant_filter,
        exact=True,
    )
    return result.count


@observe(as_type="retriever")
def search_documents(
    queries: list[str],
    document_ids: list[int] | None = None,
    top_n: int | None = None,
    lang: str | None = None,
    exclude_chunk_keys: set[str] | None = None,
) -> tuple[list[RetrievedChunk], list[str]]:
    """Multi-query search → deduplicate → rerank.

    Uses dense-only retrieval for English documents (BM25 hurts academic English)
    and hybrid retrieval for Chinese/unknown documents.

    Returns (chunks, sources_list).
    """
    effective_lang = lang or get_document_language(document_ids)
    vectorstore = get_dense_vectorstore() if effective_lang == "en" else get_vectorstore()
    logger.debug("search_documents: lang=%s mode=%s", effective_lang, "dense" if effective_lang == "en" else "hybrid")

    qdrant_filter = None
    if document_ids:
        qdrant_filter = Filter(
            must=[FieldCondition(
                key="metadata.document_id",
                match=MatchAny(any=[str(did) for did in document_ids]),
            )]
        )

    # Retrieve for each query, deduplicate by content prefix
    seen_content: set[str] = set()
    all_results = []
    handler = CallbackHandler()
    for q in queries:
        hits = vectorstore.similarity_search(q, k=RETRIEVAL_K, filter=qdrant_filter)
        for doc in hits:
            # Filter by section metadata — handles both English ("references") and
            # Chinese ("參考文獻") variants produced by the regex fallback path
            _sec = doc.metadata.get("section", "")
            if _sec in ("references", "參考文獻"):
                continue
            if _is_cover_page(doc.page_content) or _is_references_page(doc.page_content):
                continue
            # Skip chunks already seen in earlier search_report calls this session
            if exclude_chunk_keys:
                if doc.page_content[:120] in exclude_chunk_keys:
                    continue
            key = doc.page_content[:120]
            if key not in seen_content:
                seen_content.add(key)
                all_results.append(doc)

    if not all_results:
        return [], []

    # Rerank: score each chunk against every query, keep its best score
    effective_top_n = min(top_n or RERANK_TOP_N, RERANK_MAX)
    reranker = get_reranker()
    best_score: dict[str, float] = {}
    best_doc: dict[str, object] = {}
    for q in queries:
        for doc in reranker.compress_documents(all_results, q):
            key = doc.page_content[:120]
            score = doc.metadata.get("relevance_score", 0.0)
            if score > best_score.get(key, -1):
                best_score[key] = score
                best_doc[key] = doc

    # Sort by best score; move table/formula-heavy chunks to the end
    def _rank_key(d):
        key = d.page_content[:120]
        score = best_score[key]
        penalty = _is_table_or_formula_heavy(d.page_content)
        return (penalty, -score)   # False sorts before True → prose first

    ranked = sorted(best_doc.values(), key=_rank_key)
    all_results = ranked[:effective_top_n]

    # Tag table/formula chunks so the agent can see them in the result JSON
    for doc in all_results:
        doc.metadata["is_low_quality"] = _is_table_or_formula_heavy(doc.page_content)

    chunks: list[RetrievedChunk] = []
    sources: list[str] = []
    seen_sources: set[str] = set()
    for doc in all_results:
        filename = doc.metadata.get("filename", "Unknown")
        page = doc.metadata.get("page", "?")
        page_end = doc.metadata.get("page_end", page)
        page_num = int(page) + 1 if page != "?" else "?"
        page_end_num = int(page_end) + 1 if page_end != "?" else page_num
        section = doc.metadata.get("section", "unknown")
        low_q = doc.metadata.get("is_low_quality", False)
        chunks.append(RetrievedChunk(
            filename=filename, page=page_num, page_end=page_end_num,
            section=section, content=doc.page_content, is_low_quality=low_q,
        ))
        source = (
            f"{filename} p.{page_num}-{page_end_num}"
            if page_end_num != page_num else
            f"{filename} p.{page_num}"
        )
        if source not in seen_sources:
            seen_sources.add(source)
            sources.append(source)

    return chunks, sources
