"""
Batch pre-cache PDFs with LlamaParse and save results to llamacache/.
Run this for garbled/scanned documents or any PDFs you want to
prioritize LlamaParse parsing for future re-indexing.

Usage:
    python scripts/batch_llamaparse.py                    # all garbled/scanned in DB
    python scripts/batch_llamaparse.py --ids 65 470 519   # specific document IDs
    python scripts/batch_llamaparse.py --dry-run          # preview only
"""

import argparse
import os
import sys

BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, BACKEND_DIR)
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv
load_dotenv(os.path.join(BACKEND_DIR, ".env"))

from db import SessionLocal, Document
from rag import _parse_with_llamaparse, _llamaparse_cache_path
from config import settings


def main():
    parser = argparse.ArgumentParser(description="Batch LlamaParse pre-cache")
    parser.add_argument("--ids", nargs="+", type=int, help="Specific document IDs")
    parser.add_argument("--dry-run", action="store_true", help="Preview only")
    args = parser.parse_args()

    if not settings.llama_cloud_api_key.get_secret_value():
        print("ERROR: LLAMA_CLOUD_API_KEY not set in .env")
        sys.exit(1)

    db = SessionLocal()
    try:
        if args.ids:
            docs = db.query(Document).filter(Document.id.in_(args.ids)).all()
        else:
            docs = db.query(Document).filter(
                Document.quality_issue.in_(["garbled", "scanned"]),
                Document.status == "ready",
            ).all()
    finally:
        db.close()

    print(f"準備處理 {len(docs)} 份文件{'（dry-run）' if args.dry_run else ''}\n")

    ok = skipped = failed = 0
    for i, doc in enumerate(docs, 1):
        cache_path = _llamaparse_cache_path(doc.id)
        cached = os.path.exists(cache_path)
        print(f"[{i:>2}/{len(docs)}] {doc.filename[:60]}")
        print(f"        id={doc.id}  quality={doc.quality_issue or 'ok'}  "
              f"cache={'已存在' if cached else '無'}")

        if args.dry_run:
            print()
            continue

        if cached:
            print("        → 跳過（已有快取）\n")
            skipped += 1
            continue

        if not os.path.exists(doc.file_path):
            print("        → 跳過（檔案不存在）\n")
            failed += 1
            continue

        print("        → 呼叫 LlamaParse …", end="", flush=True)
        result = _parse_with_llamaparse(doc.file_path, doc_id=doc.id)
        if result:
            print(f" 完成（{len(result)} 頁，快取已存）\n")
            ok += 1
        else:
            print(" 失敗\n")
            failed += 1

    if not args.dry_run:
        print("─" * 50)
        print(f"完成: {ok}  跳過(已快取): {skipped}  失敗: {failed}")


if __name__ == "__main__":
    main()
