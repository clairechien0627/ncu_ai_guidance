"""
將舊有快取（以 PDF 檔名命名）遷移為以 doc_id 命名的新格式。

舊格式：llamacache/<filename>.md
新格式：llamacache/<doc_id>.md

Usage:
    python scripts/migrate_cache_names.py           # 預覽（dry-run）
    python scripts/migrate_cache_names.py --apply   # 實際改名
"""

import os
import sys
import argparse

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND_DIR)

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv
load_dotenv(os.path.join(BACKEND_DIR, ".env"))

from database import SessionLocal, Document
from rag import (
    _LLAMAPARSE_CACHE_DIR,
    _PYMUPDF_CACHE_DIR,
    _AZURE_DI_CACHE_DIR,
    _llamaparse_cache_path,
    _pymupdf_cache_path,
    _azure_di_cache_path,
)

CACHE_DIRS = {
    "llamaparse": _LLAMAPARSE_CACHE_DIR,
    "pymupdf4llm": _PYMUPDF_CACHE_DIR,
    "azure_di": _AZURE_DI_CACHE_DIR,
}


def stem(filename: str) -> str:
    """Remove .pdf extension to get the cache file stem."""
    if filename.lower().endswith(".pdf"):
        return filename[:-4]
    return filename


def new_path(cache_type: str, doc_id: int) -> str:
    if cache_type == "llamaparse":
        return _llamaparse_cache_path(doc_id)
    if cache_type == "pymupdf4llm":
        return _pymupdf_cache_path(doc_id)
    return _azure_di_cache_path(doc_id)


def main():
    parser = argparse.ArgumentParser(description="Migrate cache filenames to doc_id format")
    parser.add_argument("--apply", action="store_true", help="實際改名（預設 dry-run）")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        docs = db.query(Document).all()
    finally:
        db.close()

    # Build filename stem → doc_id map
    stem_to_id: dict[str, int] = {}
    for doc in docs:
        stem_to_id[stem(doc.filename)] = doc.id

    total_moved = 0
    total_skipped = 0
    total_orphan = 0

    for cache_type, cache_dir in CACHE_DIRS.items():
        if not os.path.isdir(cache_dir):
            continue

        files = os.listdir(cache_dir)
        old_files = [f for f in files if not f.split(".")[0].isdigit()]
        if not old_files:
            continue

        print(f"\n[{cache_type}] {cache_dir}")
        print(f"  舊格式檔案：{len(old_files)} 個")

        for fname in sorted(old_files):
            old_path = os.path.join(cache_dir, fname)
            # Determine stem: strip extension(s) like .md, .raw.json
            file_stem = fname
            for ext in (".raw.json", ".json", ".md"):
                if file_stem.endswith(ext):
                    file_stem = file_stem[: -len(ext)]
                    break

            doc_id = stem_to_id.get(file_stem)
            if doc_id is None:
                print(f"  [孤兒] {fname}  → 找不到對應文件，跳過")
                total_orphan += 1
                continue

            # Determine new path based on extension
            if fname.endswith(".raw.json"):
                from rag import _llamaparse_raw_cache_path
                dest = _llamaparse_raw_cache_path(doc_id)
            elif fname.endswith(".json"):
                dest = new_path(cache_type, doc_id)[:-3] + ".json"
            else:
                dest = new_path(cache_type, doc_id)

            if os.path.exists(dest):
                print(f"  [跳過] {fname}  → {os.path.basename(dest)} 已存在")
                total_skipped += 1
                continue

            print(f"  {'[改名]' if args.apply else '[預覽]'} {fname}  →  {os.path.basename(dest)}")
            if args.apply:
                os.rename(old_path, dest)
                total_moved += 1
            else:
                total_moved += 1

    print("\n" + "─" * 60)
    if args.apply:
        print(f"完成：改名 {total_moved}  跳過(已存在) {total_skipped}  孤兒 {total_orphan}")
    else:
        print(f"預覽：待改名 {total_moved}  跳過(已存在) {total_skipped}  孤兒 {total_orphan}")
        print("加上 --apply 即可實際執行。")


if __name__ == "__main__":
    main()
