"""Search and reranking logic for the RAG pipeline."""
import hashlib
import logging
import re
from typing import TypedDict

from qdrant_client.models import Filter, FieldCondition, MatchAny, RrfQuery, Rrf

from rag.store import (
    RETRIEVAL_K, RERANK_TOP_N, RERANK_MAX,
    get_vectorstore, get_dense_vectorstore, get_reranker,
    get_document_language,
)
from rag.cleaning import (
    _is_cover_page,
    _is_references_page,
    _is_table_or_formula_heavy,
)

logger = logging.getLogger(__name__)


def _chunk_key(content: str) -> str:
    """Stable dedup key for a chunk: md5 of full content."""
    return hashlib.md5(content.encode("utf-8", errors="replace")).hexdigest()


class RetrievedChunk(TypedDict):
    filename: str
    page: int | str
    page_end: int | str
    section: str
    content: str
    is_low_quality: bool


# ── Search ────────────────────────────────────────────────────────────────────

_WEIGHTED_RRF = RrfQuery(rrf=Rrf(weights=[2.0, 1.0]))


def search_documents(
    queries: list[str],
    document_ids: list[int] | None = None,
    top_n: int | None = None,
    lang: str | None = None,
    exclude_chunk_keys: set[str] | None = None,
    weighted_rrf: bool = False,
) -> tuple[list[RetrievedChunk], list[str]]:
    """Multi-query search → deduplicate → rerank.

    Uses dense-only retrieval for English documents (BM25 hurts academic English)
    and hybrid retrieval for Chinese/unknown documents.

    exclude_chunk_keys: set of md5 hashes produced by _chunk_key(doc.page_content).
    Chunks whose key appears in this set are skipped (used to avoid re-returning
    chunks already shown in a previous search iteration).

    Returns (chunks, sources_list).
    """
    effective_lang = lang or get_document_language(document_ids)
    vectorstore = get_dense_vectorstore() if effective_lang == "en" else get_vectorstore()
    fusion = _WEIGHTED_RRF if (weighted_rrf and effective_lang != "en") else None
    logger.debug("search_documents: lang=%s mode=%s weighted_rrf=%s", effective_lang, "dense" if effective_lang == "en" else "hybrid", weighted_rrf)

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
        hits = vectorstore.similarity_search(q, k=RETRIEVAL_K, filter=qdrant_filter, hybrid_fusion=fusion)
        for doc in hits:
            _sec = doc.metadata.get("section", "")
            if _sec in ("references", "參考文獻"):
                continue
            if _is_cover_page(doc.page_content) or _is_references_page(doc.page_content):
                continue
            if exclude_chunk_keys:
                if _chunk_key(doc.page_content) in exclude_chunk_keys:
                    continue
            key = _chunk_key(doc.page_content)
            if key not in seen_content:
                seen_content.add(key)
                all_results.append(doc)

    if not all_results:
        return [], []

    effective_top_n = min(top_n or RERANK_TOP_N, RERANK_MAX)
    reranker = get_reranker()
    best_score: dict[str, float] = {}
    best_doc: dict[str, object] = {}
    # Limit reranking to at most 3 queries: beyond that the marginal quality gain
    # is small but the cost grows linearly (each query scores all candidates).
    rerank_queries = queries[:3]
    for q in rerank_queries:
        for doc in reranker.compress_documents(all_results, q):
            key = _chunk_key(doc.page_content)
            score = doc.metadata.get("relevance_score", 0.0)
            if score > best_score.get(key, -1):
                best_score[key] = score
                best_doc[key] = doc

    # Pre-compute formula-heaviness once per doc to avoid calling the function
    # both in the sort key and in the metadata assignment below.
    is_heavy: dict[str, bool] = {
        key: _is_table_or_formula_heavy(doc.page_content)
        for key, doc in best_doc.items()
    }

    def _rank_key(d):
        key = _chunk_key(d.page_content)
        return (is_heavy.get(key, False), -best_score[key])

    ranked = sorted(best_doc.values(), key=_rank_key)
    all_results = ranked[:effective_top_n]

    for doc in all_results:
        key = _chunk_key(doc.page_content)
        doc.metadata["is_low_quality"] = is_heavy.get(key, False)

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
