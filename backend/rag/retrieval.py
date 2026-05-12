"""Search and reranking logic for the RAG pipeline."""
import logging
import re
from typing import TypedDict

from qdrant_client.models import Filter, FieldCondition, MatchAny

from rag.store import (
    RETRIEVAL_K, RERANK_TOP_N, RERANK_MAX,
    get_vectorstore, get_dense_vectorstore, get_reranker,
    get_document_language,
)

logger = logging.getLogger(__name__)


class RetrievedChunk(TypedDict):
    filename: str
    page: int | str
    page_end: int | str
    section: str
    content: str
    is_low_quality: bool


# ── Local filter helpers (self-contained copies to avoid circular imports) ────

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
    if any(phrase in text for phrase in _COVER_PAGE_DEFINITIVE):
        return True
    if not any(marker in text for marker in _COVER_PAGE_MARKERS):
        return False
    for line in text.splitlines():
        cjk_count = sum(1 for c in line if '一' <= c <= '鿿')
        if cjk_count >= 25:
            return False
    return True


_REFERENCES_MARKERS = (
    "參考文獻", "參考書目", "引用文獻",
    "Bibliography", "References", "REFERENCES",
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
_ZH_REFERENCES_PATTERN = re.compile(
    r"[一-鿿]{1,6}[，,：]\s*[《〈]"
    r"|[，,]\s*頁\s*\d"
    r"|[，,]\s*\d{4}\s*年[）)]"
    r"|（\d{4}\s*年\s*[），]"
)


def _is_references_page(text: str) -> bool:
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


_MATH_UNICODE = frozenset(
    "∑∫∂∇∞≤≥≠≈∝αβγδεζηθλμνξπρστφψωΩΔΓΛΣΦΨ"
    "⁰¹²³⁴⁵⁶⁷⁸⁹₀₁₂₃₄₅₆₇₈₉"
)
_LATEX_DELIMITER_RE = re.compile(
    r'^\s*(?:\$\$|\\\[|\\\]|\\begin\{[^}]+\}|\\end\{[^}]+\}|-{3,})\s*$'
)


def _is_table_or_formula_heavy(text: str) -> bool:
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


# ── Search ────────────────────────────────────────────────────────────────────

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

    seen_content: set[str] = set()
    all_results = []
    for q in queries:
        hits = vectorstore.similarity_search(q, k=RETRIEVAL_K, filter=qdrant_filter)
        for doc in hits:
            _sec = doc.metadata.get("section", "")
            if _sec in ("references", "參考文獻"):
                continue
            if _is_cover_page(doc.page_content) or _is_references_page(doc.page_content):
                continue
            if exclude_chunk_keys:
                if doc.page_content[:120] in exclude_chunk_keys:
                    continue
            key = doc.page_content[:120]
            if key not in seen_content:
                seen_content.add(key)
                all_results.append(doc)

    if not all_results:
        return [], []

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

    def _rank_key(d):
        key = d.page_content[:120]
        score = best_score[key]
        penalty = _is_table_or_formula_heavy(d.page_content)
        return (penalty, -score)

    ranked = sorted(best_doc.values(), key=_rank_key)
    all_results = ranked[:effective_top_n]

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
