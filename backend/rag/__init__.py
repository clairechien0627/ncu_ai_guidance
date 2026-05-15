"""RAG pipeline package.

External callers import directly from `rag`:
    from rag import process_pdf
    from rag import count_document_chunks, get_document_language, search_documents
    from rag import delete_document_vectors, update_document_vector_filename

Sub-modules:
    rag.cleaning   — page filters and text cleaning
    rag.section    — heading detection
    rag.store      — Qdrant vector store management
    rag.retrieval  — search and reranking
    rag.parsers    — PDF parsers (pymupdf4llm, LlamaParse, Azure DI)
    rag.chunking   — semantic chunking logic
    rag.ingestion  — process_pdf main entry point
"""
import logging

logger = logging.getLogger(__name__)

# Public API — `from rag import *` only exports these names.
# Private helpers (prefix _) are re-exported for backward compat with internal
# callers but are NOT part of the public contract.
__all__ = [
    "process_pdf",
    "extract_abstract",
    "search_documents",
    "RetrievedChunk",
    "get_document_language",
    "count_document_chunks",
    "delete_document_vectors",
    "delete_old_document_vectors",
    "delete_pending_document_vectors",
    "update_document_vector_filename",
    "get_vectorstore",
    "get_dense_embeddings",
    "get_qdrant_client",
    "COLLECTION_NAME",
    "VECTOR_SIZE",
    "RETRIEVAL_K",
    "RERANK_TOP_N",
    "parse_pdf_to_cache",
    "retry_llamaparse_warning_pages",
    "rebuild_llamaparse_md_from_raw",
    "validate_llamaparse_vs_pymupdf",
]

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

# ── Quality / page-filter / cleaning helpers (delegated to rag.cleaning) ─────

from rag.cleaning import (  # noqa: E402,F401
    _check_quality,
    _clean_text,
    _detect_language,
    _is_cover_page,
    _is_title_page_heading_only,
    _is_references_page,
    _is_reference_continuation,
    _is_table_or_formula_heavy,
    _is_toc_page,
    _is_html_data_table_page,
    _is_nmr_params_page,
    _COVER_PAGE_MARKERS,
    _COVER_PAGE_DEFINITIVE,
    _HEADING_LINE_ONLY_RE,
    _REFERENCES_MARKERS,
    _REFERENCE_HEADING_LINE_RE,
    _REFERENCES_PATTERN,
    _BULLETED_REFERENCE_RE,
    _INLINE_BULLETED_REFERENCE_RE,
    _REFERENCE_CONTINUATION_RE,
    _ZH_REFERENCES_PATTERN,
    _MATH_UNICODE,
    _LATEX_DELIMITER_RE,
    _TOC_LABEL_WORDS,
    _DOT_LEADER,
    _MD_TABLE_TOC_RE,
    _FIGURE_TABLE_ENTRY_RE,
    _MD_FIGURE_LIST_ROW_RE,
    _WORD_BOOKMARK_ERROR,
    _MD_HEADING_RE,
    _MD_INLINE_RE,
    _NMR_PARAM_KEYWORDS,
    _NMR_PARAM_LINE_RE,
)

# ── Chunking + Ingestion (delegated to rag.chunking / rag.ingestion) ─────────

from rag.chunking import (  # noqa: E402,F401
    _make_splitter,
    _remove_near_duplicate_chunks,
    _split_by_structure_and_semantics,
)

from rag.ingestion import (  # noqa: E402,F401
    extract_abstract,
    process_pdf,
)

