"""PDF parsing and cache functions.

All functions here are pure "parse PDF file → text" utilities.
They do NOT do chunking, embedding, or Qdrant indexing.
"""
import json
import logging
import os
import re
import time

from config import settings

logger = logging.getLogger(__name__)

# ── Parser cache dirs & separator ────────────────────────────────────────────

_LLAMAPARSE_CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "llamacache")
_PYMUPDF_CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "pymupdfcache")
_AZURE_DI_CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "azuredicache")

_CACHE_PAGE_SEP = "\n---PAGE---\n"


def _split_cache_pages(content: str) -> list[str]:
    """Split a .md cache file into individual page strings."""
    if _CACHE_PAGE_SEP in content:
        return [p.strip() for p in content.split(_CACHE_PAGE_SEP) if p.strip()]
    return [p.strip() for p in content.split("\n---\n") if p.strip()]


_CAPTION_PREFIX_RE = re.compile(
    r'^(?:圖|表)\s*\d|^(?:Fig(?:ure)?|Table)\.?\s*\d',
    re.IGNORECASE,
)
_NON_HEADING_EXACT = frozenset({
    "其中", "其中：", "其中:",
    "式中", "式中：",
    "注", "注：", "備注", "說明",
})


# ── Cache path helpers ────────────────────────────────────────────────────────

def _pymupdf_cache_path(doc_id: int) -> str:
    os.makedirs(_PYMUPDF_CACHE_DIR, exist_ok=True)
    return os.path.join(_PYMUPDF_CACHE_DIR, f"{doc_id}.md")


def _llamaparse_cache_path(doc_id: int) -> str:
    os.makedirs(_LLAMAPARSE_CACHE_DIR, exist_ok=True)
    return os.path.join(_LLAMAPARSE_CACHE_DIR, f"{doc_id}.md")


def _llamaparse_raw_cache_path(doc_id: int) -> str:
    os.makedirs(_LLAMAPARSE_CACHE_DIR, exist_ok=True)
    return os.path.join(_LLAMAPARSE_CACHE_DIR, f"{doc_id}.raw.json")


def _azure_di_cache_path(doc_id: int) -> str:
    os.makedirs(_AZURE_DI_CACHE_DIR, exist_ok=True)
    return os.path.join(_AZURE_DI_CACHE_DIR, f"{doc_id}.md")


# ── Parser utilities ──────────────────────────────────────────────────────────

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


# ── Parsers ───────────────────────────────────────────────────────────────────

def _parse_with_pymupdf4llm(file_path: str, force: bool = False, doc_id: int = 0) -> list:
    """Primary PDF parser: local, free, produces Markdown with # headings."""
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


def _parse_with_azure_di(file_path: str, force: bool = False, doc_id: int = 0) -> list:
    """Parse PDF using Azure Document Intelligence layout model."""
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


def _parse_with_llamaparse(file_path: str, force: bool = False, doc_id: int = 0) -> list:
    """Fallback PDF parser using LlamaParse (cloud Vision AI)."""
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
    json_cache = cache_path[:-3] + ".json"
    if not force and os.path.exists(cache_path):
        logger.info("LlamaParse cache hit for '%s'", os.path.basename(file_path))
        with open(cache_path, "r", encoding="utf-8") as f:
            content = f.read()
        pages = [_strip_code_fence(p) for p in _split_cache_pages(content)]
        pages = [p for p in pages if p]
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
            logger.warning(
                "LlamaParse returned no usable page text for '%s'; raw response saved to %s",
                os.path.basename(file_path),
                raw_cache_path,
            )
            return []

        _raw_pos: dict[int, tuple[int, int]] = {}
        for r_idx, result in enumerate(json_results):
            for p_idx, page in enumerate(result.get("pages", [])):
                if isinstance(page, dict):
                    _raw_pos[page.get("page", p_idx + 1)] = (r_idx, p_idx)

        pages_mut = [list(p) for p in pages]
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
                    new_pgs: dict[int, dict] = {}
                    for rr in (retry_results if isinstance(retry_results, list) else [retry_results]):
                        for pg in rr.get("pages", []):
                            if isinstance(pg, dict) and pg.get("page") is not None:
                                new_pgs[pg["page"]] = pg
                    recovered = set()
                    for row in pages_mut:
                        actual_pn = row[0]
                        if actual_pn not in remaining_ghosts:
                            continue
                        llama_pg = actual_pn + 1
                        new_pg = new_pgs.get(llama_pg)
                        if new_pg is None:
                            continue
                        new_text = _page_text_from_payload(new_pg)
                        if new_text:
                            row[1] = new_text
                            row[2] = True
                            recovered.add(actual_pn)
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
    """Re-submit only the WARNING / NO_CONTENT_HERE pages to LlamaParse."""
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

    warning_map: dict[int, tuple[int, int]] = {}
    for r_idx, result in enumerate(results):
        for p_idx, page in enumerate(result.get("pages", [])):
            if (
                page.get("noTextContent") is False
                and page.get("status") == "WARNING"
            ):
                llama_page = page.get("page", p_idx + 1)
                warning_map[llama_page] = (r_idx, p_idx)

    if not warning_map:
        logger.info("No WARNING pages found in raw.json for doc_id=%d", doc_id)
        return {"retried": [], "recovered": 0, "still_failed": 0}

    all_target_0idx = sorted(p - 1 for p in warning_map.keys())
    logger.info(
        "Retrying %d WARNING page(s) for doc_id=%d: 0-indexed=%s",
        len(all_target_0idx), doc_id, all_target_0idx,
    )

    import httpx
    import time as _time

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

    remaining = set(warning_map.keys())
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

    with open(raw_cache_path, "w", encoding="utf-8") as f:
        json.dump(results if isinstance(raw_data, list) else results[0], f, ensure_ascii=False, indent=2)
    logger.info("Updated raw.json saved for doc_id=%d (recovered=%d still_failed=%d)",
                doc_id, recovered, still_failed)

    rebuild_llamaparse_md_from_raw(file_path, doc_id)

    return {
        "retried": all_target_0idx,
        "recovered": recovered,
        "still_failed": still_failed,
    }


def rebuild_llamaparse_md_from_raw(file_path: str, doc_id: int) -> list:
    """Rebuild the LlamaParse .md cache from an existing raw.json without calling the API."""
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

    with open(cache_path, "w", encoding="utf-8") as f:
        f.write(_CACHE_PAGE_SEP.join(text for _, text, _ in pages))

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
    """Read parsed pages from a cache file only — never calls any API."""
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
    """Compare LlamaParse and pymupdf4llm page-by-page and return suspicious pages."""
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
            continue
        llama_text = llama_map.get(pg, "")
        llama_chars = len(llama_text.strip())
        if llama_chars >= sparse_threshold:
            continue
        severity = "error" if llama_chars < 30 else "warn"
        issues.append({
            "page": pg + 1,
            "llama_chars": llama_chars,
            "pymupdf_chars": pymupdf_chars,
            "llama_preview": llama_text.strip()[:120],
            "pymupdf_preview": pymupdf_text.strip()[:120],
            "severity": severity,
        })
    issues.sort(key=lambda x: x["page"])
    return issues


def parse_pdf_to_cache(file_path: str, parser: str, doc_id: int = 0) -> dict:
    """Explicitly run one parser and (re)write its cache."""
    # Import quality helpers from __init__ to avoid duplicating them here.
    # These are plain text-analysis functions with no circular-import risk
    # because parsers.py does not import from rag.__init__.
    from langchain_community.document_loaders import PyMuPDFLoader

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

    # Defer import to avoid circular imports: rag.__init__ imports from rag.parsers
    # at module load time, so we cannot do a top-level `from rag import ...` here.
    # A function-level import works fine because by the time parse_pdf_to_cache is
    # called, both modules are fully initialised.
    import importlib
    _rag = importlib.import_module("rag")
    _detect_language = _rag._detect_language  # type: ignore[attr-defined]
    _clean_text = _rag._clean_text  # type: ignore[attr-defined]
    _check_quality = _rag._check_quality  # type: ignore[attr-defined]

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
