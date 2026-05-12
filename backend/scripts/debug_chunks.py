"""
Preview chunking results without storing anything to Qdrant.

Usage examples:
    # Interactive PDF mode (looks up DB for cache):
    python scripts/debug_chunks.py

    # Point directly at a cache .md file:
    python scripts/debug_chunks.py --md backend/llamacache/42.md --parser llamaparse

    # Load by doc-id (reads the best available cache, no API calls):
    python scripts/debug_chunks.py --doc-id 42 --parser azure_di

    # Show full chunk text (no truncation):
    python scripts/debug_chunks.py --md ... --full

    # Force language:
    python scripts/debug_chunks.py --lang zh

Output: dropped pages, heading candidates, section map, per-chunk detail, summary stats.
"""

import argparse
import os
import sys
import textwrap

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND_DIR)

from dotenv import load_dotenv
load_dotenv(os.path.join(BACKEND_DIR, ".env"))

from langchain_community.document_loaders import PyMuPDFLoader
from langchain_core.documents import Document as LCDocument

from rag import (
    _clean_text,
    _detect_language,
    _is_cover_page,
    _is_title_page_heading_only,
    _is_toc_page,
    _is_html_data_table_page,
    _is_nmr_params_page,
    _is_table_or_formula_heavy,
    _extract_headings_by_font,
    _extract_candidate_headings,
    _extract_headings_from_markdown,
    _build_page_section_map,
    _split_by_structure_and_semantics,
    _check_quality,
    _parse_with_pymupdf4llm,
    _parse_with_llamaparse,
    _parse_with_azure_di,
    _llamaparse_cache_path,
    _pymupdf_cache_path,
    _azure_di_cache_path,
    _read_from_cache,
    validate_llamaparse_vs_pymupdf,
)
from config import settings

SEP = "─" * 72


def _bar(ratio: float, width: int = 20) -> str:
    filled = round(ratio * width)
    return "#" * filled + "-" * (width - filled)


def _safe_print(text: str) -> None:
    try:
        print(text)
    except UnicodeEncodeError:
        enc = sys.stdout.encoding or "utf-8"
        print(text.encode(enc, errors="replace").decode(enc, errors="replace"))


def _load_from_md(md_path: str, parser: str) -> list:
    """Load pages from a raw .md cache file (split on \\n---\\n page separator)."""
    # Resolve: try as-is first, then relative to BACKEND_DIR
    resolved = md_path
    if not os.path.exists(resolved):
        alt = os.path.join(BACKEND_DIR, md_path)
        if os.path.exists(alt):
            resolved = alt
        else:
            abs_tried = os.path.abspath(md_path)
            print(f"[錯誤] 找不到 MD 檔案: {abs_tried}", file=sys.stderr)
            return []
    with open(resolved, "r", encoding="utf-8") as f:
        content = f.read()
    pages = [p.strip() for p in content.split("\n---\n") if p.strip()]
    tag = {parser: True} if parser in ("pymupdf4llm", "llamaparse", "azure_di") else {"pymupdf4llm": True}
    docs = [
        LCDocument(page_content=p, metadata={"page": i, "source": resolved, **tag})
        for i, p in enumerate(pages)
    ]
    print(f"  Source       : {resolved}")
    print(f"  Parser tag   : {list(tag.keys())[0]}")
    print(f"  Pages loaded : {len(docs)}")
    return docs


def process(
    pdf_path: str | None,
    lang_override: str | None,
    full_output: bool,
    md_path: str | None = None,
    parser_override: str | None = None,
    doc_id_override: int | None = None,
) -> None:
    docs: list = []
    parser_used = ""

    # ── 1. Load docs ──────────────────────────────────────────────────────────
    if md_path:
        # Direct MD file path
        parser_tag = parser_override or "pymupdf4llm"
        docs = _load_from_md(md_path, parser_tag)
        parser_used = f"{parser_tag} (md file)"

    elif doc_id_override:
        # Load from existing cache by doc-id
        _doc_id = doc_id_override
        _fake_path = f"<doc_id={_doc_id}>"
        parser_tag = parser_override or "auto"

        if parser_tag == "auto":
            # Priority: azure_di > llamaparse > pymupdf4llm
            for p in ("azure_di", "llamaparse", "pymupdf4llm"):
                docs = _read_from_cache(_fake_path, p, _doc_id)
                if docs:
                    parser_used = f"{p} (cache)"
                    break
        else:
            docs = _read_from_cache(_fake_path, parser_tag, _doc_id)
            parser_used = f"{parser_tag} (cache)"

        if not docs:
            print(f"[錯誤] doc-id {_doc_id} 沒有可用的快取，請先解析", file=sys.stderr)
            return
        print(f"  Doc ID       : {_doc_id}")
        print(f"  Parser       : {parser_used}")
        print(f"  Pages loaded : {len(docs)}")

    else:
        # Original PDF path mode
        if not pdf_path or not os.path.exists(pdf_path):
            print(f"[錯誤] 找不到檔案: {pdf_path}", file=sys.stderr)
            return

        print(f"\n載入: {pdf_path}")
        from database import SessionLocal, Document as _Doc
        _db = SessionLocal()
        try:
            _doc = _db.query(_Doc).filter(_Doc.file_path == pdf_path).first()
            _doc_id = _doc.id if _doc else 0
        finally:
            _db.close()
        if not _doc_id:
            print("  [警告] 找不到 DB 記錄，快取功能停用")

        forced = parser_override
        if forced == "azure_di":
            docs = _parse_with_azure_di(pdf_path, doc_id=_doc_id)
            parser_used = "azure_di"
        elif forced == "llamaparse":
            docs = _parse_with_llamaparse(pdf_path, doc_id=_doc_id)
            parser_used = "llamaparse"
        elif forced == "pymupdf4llm":
            docs = _parse_with_pymupdf4llm(pdf_path, doc_id=_doc_id)
            parser_used = "pymupdf4llm"
        else:
            # Auto: prefer existing cloud caches
            if _doc_id and os.path.exists(_azure_di_cache_path(_doc_id)):
                docs = _read_from_cache(pdf_path, "azure_di", _doc_id)
                parser_used = "azure_di (cache)"
            elif _doc_id and os.path.exists(_llamaparse_cache_path(_doc_id)):
                docs = _parse_with_llamaparse(pdf_path, doc_id=_doc_id)
                parser_used = "llamaparse (cache)"
            if not docs:
                docs = _parse_with_pymupdf4llm(pdf_path, doc_id=_doc_id)
                parser_used = "pymupdf4llm"
            if not docs:
                docs = PyMuPDFLoader(pdf_path).load()
                parser_used = "PyMuPDFLoader"

        print(f"  Parser       : {parser_used}")
        print(f"  Pages loaded : {len(docs)}")

    if not docs:
        return

    # ── 2. Language + clean ───────────────────────────────────────────────────
    lang = lang_override or _detect_language(docs)
    print(f"  Language     : {lang}")

    for doc in docs:
        doc.page_content = _clean_text(doc.page_content, lang)

    # ── 3. Page-level filters ─────────────────────────────────────────────────
    kept, dropped = [], []
    for d in docs:
        text = d.page_content
        pg = d.metadata.get("page", "?")
        if not text:
            dropped.append((pg, "empty", ""))
        elif _is_cover_page(text):
            dropped.append((pg, "cover", text))
        elif _is_title_page_heading_only(text):
            dropped.append((pg, "title_page", text))
        elif _is_toc_page(text, lang):
            dropped.append((pg, "toc", text))
        elif _is_html_data_table_page(text):
            dropped.append((pg, "html_table", text))
        elif _is_nmr_params_page(text):
            dropped.append((pg, "nmr_params", text))
        else:
            kept.append(d)

    quality_issue = _check_quality(kept)

    if quality_issue is None and any(d.metadata.get("pymupdf4llm") for d in kept):
        from rag import _extract_headings_from_markdown as _efm
        md_headings = _efm(kept)
        if len(md_headings) >= 3:
            heading_text = " ".join(h["text"] for h in md_headings)
            total_h = sum(1 for c in heading_text if not c.isspace())
            if total_h > 0 and heading_text.count('�') / total_h > 0.25:
                quality_issue = "garbled"
                print(f"  [Extra check] 標題含 U+FFFD 替換字元 → garbled")

    print(f"  Quality issue: {quality_issue or 'None（正常）'}")
    print(f"  Pages kept   : {len(kept)}  (dropped {len(dropped)})")

    if dropped:
        print("  Dropped pages:")
        for pg, reason, text in dropped:
            pn = int(pg) + 1 if pg != "?" else "?"
            preview = text[:120].replace("\n", " ").strip()
            print(f"    p.{pn:<4} [{reason}] {preview}")

    docs = kept

    # ── 4. Heading candidates ─────────────────────────────────────────────────
    is_markdown = any(d.metadata.get("pymupdf4llm") or d.metadata.get("llamaparse") for d in docs)
    if is_markdown:
        candidates = _extract_headings_from_markdown(docs)
        source = "markdown"
    else:
        font_candidates = _extract_headings_by_font(pdf_path or "")
        regex_candidates = _extract_candidate_headings(docs)
        candidates = font_candidates if font_candidates else regex_candidates
        source = "font" if font_candidates else "regex"

    print(f"\nHeading candidates ({len(candidates)}, source={source}):")
    if candidates:
        for c in candidates:
            print(f"  p.{c['page']+1:<4} {c['text']}")
    else:
        print("  (none — section map 將全為 unknown)")

    # ── 5. Section map ────────────────────────────────────────────────────────
    print("\nBuilding section map …")
    page_section_map = _build_page_section_map(docs, lang, file_path=pdf_path)
    section_counts: dict[str, int] = {}
    for v in page_section_map.values():
        section_counts[v] = section_counts.get(v, 0) + 1
    print("  Section → pages:")
    for sec, cnt in sorted(section_counts.items(), key=lambda x: -x[1]):
        print(f"    {sec:<22} {cnt} page(s)")

    # ── 6. Chunking ───────────────────────────────────────────────────────────
    print("\nChunking …")
    splits = _split_by_structure_and_semantics(docs, page_section_map, lang)
    print(f"  Total chunks : {len(splits)}\n")

    char_counts = [len(s.page_content) for s in splits]
    max_chars = max(char_counts) if char_counts else 1

    for i, chunk in enumerate(splits, 1):
        section  = chunk.metadata.get("section", "unknown")
        page     = chunk.metadata.get("page", "?")
        page_end = chunk.metadata.get("page_end", page)
        page_num     = int(page) + 1     if page     != "?" else "?"
        page_end_num = int(page_end) + 1 if page_end != "?" else page_num
        page_label = (
            f"{page_num}-{page_end_num}" if page_end_num != page_num else str(page_num)
        )
        chars  = len(chunk.page_content)
        low_q  = _is_table_or_formula_heavy(chunk.page_content)
        bar    = _bar(chars / max_chars)

        print(SEP)
        print(f"[{i:>3}] section={section:<18} page={page_label:<7}  chars={chars:>5}  "
              f"{'[low-quality]' if low_q else ''}")
        print(f"      {bar}  {chars}")

        if full_output:
            for line in chunk.page_content.splitlines():
                _safe_print(f"      {line}")
        else:
            preview = chunk.page_content[:300].replace("\n", " ").strip()
            if len(chunk.page_content) > 300:
                preview += " …"
            _safe_print(f"      {textwrap.fill(preview, width=68, subsequent_indent='      ')}")

    print(SEP)

    if char_counts:
        avg = sum(char_counts) / len(char_counts)
        print(f"\nSummary  min={min(char_counts)}  avg={avg:.0f}  max={max(char_counts)}  "
              f"total={sum(char_counts)}")

    sec_chunks: dict[str, list[int]] = {}
    for chunk in splits:
        sec = chunk.metadata.get("section", "unknown")
        sec_chunks.setdefault(sec, []).append(len(chunk.page_content))
    print("\nChunks per section:")
    for sec, sizes in sorted(sec_chunks.items(), key=lambda x: -len(x[1])):
        avg_s = sum(sizes) / len(sizes)
        print(f"  {sec:<24} {len(sizes):>3} chunks   avg {avg_s:.0f} chars")
    print()


def _run_validate(args) -> None:
    """
    Compare llamaparse vs pymupdf4llm page-by-page for a given doc-id,
    and print pages where llamaparse looks suspiciously sparse.

    Usage:
        python scripts/debug_chunks.py --validate --doc-id 507
        python scripts/debug_chunks.py --validate --doc-id 507 --output validate_507.txt
    """
    doc_id = args.doc_id
    if not doc_id:
        print("[錯誤] --validate 需要搭配 --doc-id N", file=sys.stderr)
        return

    fake_path = f"<doc_id={doc_id}>"

    llama_docs = _read_from_cache(fake_path, "llamaparse", doc_id)
    pymupdf_docs = _read_from_cache(fake_path, "pymupdf4llm", doc_id)

    if not llama_docs:
        print(f"[錯誤] 找不到 doc-id {doc_id} 的 llamaparse 快取", file=sys.stderr)
        return
    if not pymupdf_docs:
        print(f"[警告] 找不到 doc-id {doc_id} 的 pymupdf4llm 快取，嘗試本地解析…", file=sys.stderr)
        # Build a local parse path if we have the DB
        try:
            from database import SessionLocal, Document as _Doc
            _db = SessionLocal()
            try:
                doc = _db.query(_Doc).filter(_Doc.id == doc_id).first()
                pdf_path = doc.file_path if doc else None
            finally:
                _db.close()
            if pdf_path and os.path.exists(pdf_path):
                pymupdf_docs = _parse_with_pymupdf4llm(pdf_path, doc_id=doc_id)
        except Exception as e:
            print(f"[警告] 無法取得 pymupdf4llm 解析: {e}", file=sys.stderr)

    if not pymupdf_docs:
        print(f"[錯誤] 無法取得 pymupdf4llm 資料，無法比對", file=sys.stderr)
        return

    print(f"\n{'='*72}")
    print(f"LlamaParse vs pymupdf4llm 逐頁比對  (doc-id={doc_id})")
    print(f"  llamaparse pages : {len(llama_docs)}")
    print(f"  pymupdf pages    : {len(pymupdf_docs)}")
    print(f"{'='*72}\n")

    issues = validate_llamaparse_vs_pymupdf(llama_docs, pymupdf_docs)

    if not issues:
        _safe_print("[OK] 沒有發現疑似失敗的頁面（所有 llamaparse 頁面都有足夠文字）\n")
        return

    _safe_print(f"[WARN] 發現 {len(issues)} 頁可疑（llamaparse 文字量遠低於 pymupdf4llm）：\n")
    errors = [i for i in issues if i["severity"] == "error"]
    warns  = [i for i in issues if i["severity"] == "warn"]

    for issue in issues:
        tag = "[ERR] " if issue["severity"] == "error" else "[WARN]"
        _safe_print(
            f"{tag} p.{issue['page']:>3}  "
            f"llamaparse={issue['llama_chars']:>4} chars  "
            f"pymupdf={issue['pymupdf_chars']:>5} chars"
        )
        if issue["llama_preview"]:
            _safe_print(f"       LlamaParse  : {issue['llama_preview'][:100]}")
        _safe_print(f"       pymupdf4llm : {issue['pymupdf_preview'][:100]}")
        print()

    print(f"{'─'*72}")
    _safe_print(f"  [ERR]  llamaparse < 30 chars  : {len(errors)} pages")
    _safe_print(f"  [WARN] llamaparse 30-79 chars  : {len(warns)} pages")
    _safe_print(f"\n建議：對上述頁面重新執行 llamaparse 解析，"
                f"或在前端選「解析比對」確認 llamaparse 輸出是否正常。\n")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Preview PDF chunking (no Qdrant writes)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--md",
        metavar="PATH",
        help="直接指定快取 .md 檔（跳過 PDF 載入和 DB 查詢）",
    )
    parser.add_argument(
        "--doc-id",
        type=int,
        metavar="N",
        help="用 doc-id 載入現有快取（不呼叫任何 API）",
    )
    parser.add_argument(
        "--parser",
        choices=["pymupdf4llm", "llamaparse", "azure_di", "auto"],
        default=None,
        help="指定 parser 類型（影響 metadata tag 和標題偵測邏輯）",
    )
    parser.add_argument(
        "--lang",
        choices=["en", "zh"],
        default=None,
        help="強制指定語言（不自動偵測）",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="輸出每個 chunk 的完整文字（不截斷）",
    )
    parser.add_argument(
        "--output",
        metavar="PATH",
        help="將所有輸出寫入指定檔案（UTF-8），不指定則印到 terminal",
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="比對 llamaparse 和 pymupdf4llm 的逐頁輸出，找出 llamaparse 疑似失敗的頁面",
    )
    args = parser.parse_args()

    _out_file = None
    _orig_stdout = sys.stdout
    if args.output:
        _out_file = open(args.output, "w", encoding="utf-8")
        sys.stdout = _out_file
        args.full = True  # 寫檔時自動完整輸出
        print(f"# debug_chunks output — {args.output}\n")

    try:
        if args.validate:
            _run_validate(args)
            return

        if args.md:
            process(
                pdf_path=None,
                lang_override=args.lang,
                full_output=args.full,
                md_path=args.md,
                parser_override=args.parser or "pymupdf4llm",
            )
            return

        if args.doc_id:
            process(
                pdf_path=None,
                lang_override=args.lang,
                full_output=args.full,
                doc_id_override=args.doc_id,
                parser_override=args.parser or "auto",
            )
            return

        # Interactive PDF path mode
        if _out_file:
            print("# Interactive mode — PDF path 從 terminal 讀取\n")
        else:
            print("輸入 PDF 路徑（可連續測多個，輸入 q 結束）")
        while True:
            try:
                prompt_out = _orig_stdout if _out_file else sys.stdout
                prompt_out.write("\nPDF path: ")
                prompt_out.flush()
                raw = sys.stdin.readline().strip().strip('"').strip("'")
            except (EOFError, KeyboardInterrupt):
                break
            if not raw or raw.lower() in ("q", "quit", "exit"):
                break
            process(
                pdf_path=raw,
                lang_override=args.lang,
                full_output=args.full,
                parser_override=args.parser,
            )
    finally:
        if _out_file:
            sys.stdout = _orig_stdout
            _out_file.close()
            print(f"[完成] 輸出已寫入 {args.output}")


if __name__ == "__main__":
    main()
