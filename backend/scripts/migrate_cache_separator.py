"""
將所有 .md 快取檔從舊分頁符（\\n---\\n）遷移到新格式（\\n---PAGE---\\n）。

舊格式與 LlamaParse 輸出的 markdown 水平線衝突，導致錯誤分頁。
新格式使用不會出現在正文的專用分隔符。

只處理含有舊分隔符且尚未使用新分隔符的檔案，已是新格式的直接跳過。

Usage:
    cd backend
    source .venv/Scripts/activate

    # 先 dry-run 看有哪些檔案會被更新
    python scripts/migrate_cache_separator.py --dry-run

    # 實際遷移
    python scripts/migrate_cache_separator.py
"""

import argparse
import os
import sys

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

OLD_SEP = "\n---\n"
NEW_SEP = "\n---PAGE---\n"

CACHE_DIRS = [
    os.path.join(BACKEND_DIR, "llamacache"),
    os.path.join(BACKEND_DIR, "pymupdfcache"),
]


def migrate(dry_run: bool) -> None:
    updated = skipped_new = skipped_no_old = errors = 0

    for cache_dir in CACHE_DIRS:
        if not os.path.exists(cache_dir):
            print(f"[SKIP] 目錄不存在：{cache_dir}")
            continue

        md_files = sorted(f for f in os.listdir(cache_dir) if f.endswith(".md"))
        print(f"\n{cache_dir}  ({len(md_files)} 個 .md 檔)")

        for fname in md_files:
            path = os.path.join(cache_dir, fname)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    content = f.read()
            except Exception as e:
                print(f"  [ERR ] {fname}: 讀取失敗 — {e}")
                errors += 1
                continue

            if NEW_SEP in content:
                skipped_new += 1
                continue  # already migrated

            if OLD_SEP not in content:
                skipped_no_old += 1
                continue  # single-page or no separator at all

            new_content = content.replace(OLD_SEP, NEW_SEP)
            old_pages = content.count(OLD_SEP) + 1
            print(f"  [{'DRY' if dry_run else 'OK '}] {fname}  ({old_pages} 頁)")

            if not dry_run:
                try:
                    with open(path, "w", encoding="utf-8") as f:
                        f.write(new_content)
                except Exception as e:
                    print(f"  [ERR ] {fname}: 寫入失敗 — {e}")
                    errors += 1
                    continue

            updated += 1

    print(f"\n{'─' * 60}")
    print(f"{'dry-run 結果' if dry_run else '遷移完成'}")
    print(f"  更新{'（預覽）' if dry_run else ''}  : {updated} 個")
    print(f"  已是新格式 (跳過) : {skipped_new} 個")
    print(f"  無分隔符 (跳過)   : {skipped_no_old} 個")
    if errors:
        print(f"  錯誤              : {errors} 個")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="遷移 .md 快取分頁符",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--dry-run", action="store_true", help="只列出會被更新的檔案，不實際寫入")
    args = parser.parse_args()

    print(f"{'=' * 60}")
    print(f"快取分頁符遷移工具  {'[DRY-RUN]' if args.dry_run else ''}")
    print(f"  {OLD_SEP!r}  →  {NEW_SEP!r}")
    print(f"{'=' * 60}")

    migrate(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
