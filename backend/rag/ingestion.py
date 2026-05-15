"""PDF ingestion pipeline: load → clean → section → chunk → embed → store."""
import logging
import os
import re
from typing import Callable

from langchain_community.document_loaders import PyMuPDFLoader

from config import settings
from rag.chunking import _split_by_structure_and_semantics
from rag.cleaning import (
    _check_quality,
    _clean_text,
    _detect_language,
    _is_cover_page,
    _is_title_page_heading_only,
    _is_toc_page,
    _is_html_data_table_page,
    _is_nmr_params_page,
)
from rag.parsers import (
    _azure_di_cache_path,
    _llamaparse_cache_path,
    _parse_with_azure_di,
    _parse_with_llamaparse,
    _parse_with_pymupdf4llm,
    _read_from_cache,
)
from rag.section import (
    _build_page_section_map,
    _extract_headings_from_markdown,
)
from rag.store import (
    delete_pending_document_vectors,
    get_vectorstore,
)

logger = logging.getLogger(__name__)


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

    _SKIP_PREFIXES = (
        "感謝", "致謝", "謹致謝意", "誌謝",   # acknowledgments
        "目錄", "目次",                          # table of contents
        "圖目錄", "表目錄", "附錄",              # figure / table / appendix lists
    )
    for doc in docs[:2]:
        for para in doc.page_content.split("\n\n"):
            para = para.strip()
            if len(para) >= 150 and not any(para.startswith(p) for p in _SKIP_PREFIXES):
                return para[:2000]
    return None


def process_pdf(
    file_path: str,
    document_id: int,
    on_stage: Callable[[str], None] | None = None,
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
                replacement = heading_text.count('â€¦')
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
        elif settings.llama_cloud_api_key.get_secret_value():
            _stage("LlamaParse 重新解析")
            alt_docs = _parse_with_llamaparse(file_path, doc_id=document_id)
            fallback = "llamaparse"
        elif settings.azure_document_intelligence_endpoint and settings.azure_document_intelligence_key.get_secret_value():
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
    try:
        get_vectorstore().add_documents(splits)
    except Exception as exc:
        # Clean up any vectors that were partially written before the failure.
        # reindex_ts stamps each split so delete_pending_document_vectors can
        # target only this ingestion run without touching previously indexed chunks.
        logger.error("add_documents failed for document_id=%s: %s", document_id, exc)
        if reindex_ts is not None:
            try:
                delete_pending_document_vectors(document_id, reindex_ts)
            except Exception as cleanup_exc:
                logger.warning("cleanup of partial vectors failed: %s", cleanup_exc)
        raise
    return len(splits), abstract, quality_issue, parser_used
