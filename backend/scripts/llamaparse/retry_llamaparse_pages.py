"""
針對特定文件，只對 LlamaParse 回傳 WARNING/NO_CONTENT_HERE 的頁面重跑。
不會重送整份 PDF，只送出那幾頁，省 API 用量。

Usage:
    cd backend
    source .venv/Scripts/activate

    # 重跑單一文件
    python scripts/retry_llamaparse_pages.py --doc-id 117

    # 重跑多個文件
    python scripts/retry_llamaparse_pages.py --doc-id 117 118 121

    # 先 dry-run 看哪幾頁會被重跑（不實際呼叫 API）
    python scripts/retry_llamaparse_pages.py --doc-id 117 --dry-run
"""

import argparse
import json
import os
import sys

BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, BACKEND_DIR)

from dotenv import load_dotenv
load_dotenv(os.path.join(BACKEND_DIR, ".env"))


def _safe_print(text: str) -> None:
    try:
        print(text)
    except UnicodeEncodeError:
        enc = sys.stdout.encoding or "utf-8"
        print(text.encode(enc, errors="replace").decode(enc, errors="replace"))


def _find_warning_pages(raw_cache_path: str) -> list[int]:
    """Return 1-indexed page numbers that are WARNING/NO_CONTENT_HERE."""
    if not os.path.exists(raw_cache_path):
        return []
    with open(raw_cache_path, encoding="utf-8") as f:
        data = json.load(f)
    results = data if isinstance(data, list) else [data]
    pages = []
    for result in results:
        for pg in result.get("pages", []):
            if pg.get("noTextContent") is False and pg.get("status") == "WARNING":
                pages.append(pg.get("page", "?"))
    return pages


def process_doc(doc_id: int, dry_run: bool) -> None:
    from rag import (
        _llamaparse_raw_cache_path,
        _llamaparse_cache_path,
        retry_llamaparse_warning_pages,
        rebuild_llamaparse_md_from_raw,
    )
    from db import SessionLocal, Document

    raw_path = _llamaparse_raw_cache_path(doc_id)
    md_path  = _llamaparse_cache_path(doc_id)
    warning_pages = _find_warning_pages(raw_path)

    # Case 1: no WARNING pages but .md is missing — rebuild from existing raw.json
    if not warning_pages and not os.path.exists(md_path):
        if not os.path.exists(raw_path):
            _safe_print(f"  [doc {doc_id}] SKIP — raw.json 和 .md 都不存在")
            return
        _safe_print(f"  [doc {doc_id}] .md 缺失（raw.json 完整） — 直接重建 .md")
        if dry_run:
            _safe_print(f"  [doc {doc_id}] (dry-run，跳過)")
            return
        # Need file_path for pymupdf fallback
        db = SessionLocal()
        try:
            doc = db.query(Document).filter(Document.id == doc_id).first()
            file_path = doc.file_path if doc else None
        finally:
            db.close()
        if not file_path or not os.path.exists(file_path):
            _safe_print(f"  [doc {doc_id}] PDF 不存在，改用空路徑重建（無 pymupdf fallback）")
            file_path = ""
        docs = rebuild_llamaparse_md_from_raw(file_path, doc_id)
        _safe_print(f"  [doc {doc_id}] 重建完成：{len(docs)} 頁，請在前端重新嵌入以更新向量庫")
        return

    # Case 2: no WARNING pages, .md already exists — nothing to do
    if not warning_pages:
        _safe_print(f"  [doc {doc_id}] OK — 沒有 WARNING 頁面，.md 已存在")
        return

    _safe_print(f"  [doc {doc_id}] 找到 {len(warning_pages)} 個 WARNING 頁面："
                f" {warning_pages[:10]}{'...' if len(warning_pages) > 10 else ''}")

    if dry_run:
        _safe_print(f"  [doc {doc_id}] (dry-run，跳過 API 呼叫)")
        return

    # Get PDF path from DB
    db = SessionLocal()
    try:
        doc = db.query(Document).filter(Document.id == doc_id).first()
        if not doc:
            _safe_print(f"  [doc {doc_id}] 找不到 DB 記錄")
            return
        file_path = doc.file_path
    finally:
        db.close()

    if not file_path or not os.path.exists(file_path):
        _safe_print(f"  [doc {doc_id}] PDF 檔案不存在：{file_path}")
        return

    _safe_print(f"  [doc {doc_id}] 送出 0-indexed 頁碼：{sorted(p-1 for p in warning_pages)}")
    _safe_print(f"  [doc {doc_id}] 等待 LlamaParse 回應中...")

    result = retry_llamaparse_warning_pages(file_path, doc_id)

    _safe_print(f"  [doc {doc_id}] 完成：recovered={result['recovered']}"
                f"  still_failed={result['still_failed']}")
    _safe_print(f"  [doc {doc_id}] .md 快取已重建完成，請在前端重新嵌入以更新向量庫")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="只重跑 LlamaParse WARNING 頁面",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--doc-id", type=int, nargs="+", required=True, metavar="N",
        help="要處理的文件編號（可多個）",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="只列出會被重跑的頁面，不實際呼叫 API",
    )
    args = parser.parse_args()

    _safe_print(f"{'=' * 60}")
    _safe_print(f"LlamaParse WARNING 頁面重跑工具")
    _safe_print(f"  文件數: {len(args.doc_id)}")
    _safe_print(f"  dry-run: {args.dry_run}")
    _safe_print(f"{'=' * 60}")

    for doc_id in args.doc_id:
        process_doc(doc_id, args.dry_run)

    _safe_print(f"\n完成")


if __name__ == "__main__":
    main()
