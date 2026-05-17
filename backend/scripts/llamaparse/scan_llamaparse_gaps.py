"""
掃描所有有 llamaparse 快取的文件，找出「llamaparse 頁面近乎空白但 pymupdf4llm 有文字」的文件。

這通常是 LlamaParse vision API 靜默失敗（回傳 [0 x 0] 圖片佔位），
導致部分文字頁被錯誤地當成空白圖片丟棄。

Usage:
    cd backend
    source .venv/Scripts/activate
    python scripts/scan_llamaparse_gaps.py

    # 輸出到檔案
    python scripts/scan_llamaparse_gaps.py --output gap_report.txt

    # 只顯示有問題的文件（隱藏 OK 的）
    python scripts/scan_llamaparse_gaps.py --problems-only

    # 調整判定門檻（預設 sparse=80, rich=150）
    python scripts/scan_llamaparse_gaps.py --sparse 60 --rich 120
"""

import argparse
import os
import sys

BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, BACKEND_DIR)

from dotenv import load_dotenv
load_dotenv(os.path.join(BACKEND_DIR, ".env"))

from langchain_core.documents import Document as LCDocument


def _load_md_cache(path: str, tag_key: str) -> list:
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        content = f.read()
    pages = [p.strip() for p in content.split("\n---\n") if p.strip()]
    return [
        LCDocument(page_content=p, metadata={"page": i, tag_key: True})
        for i, p in enumerate(pages)
    ]


def scan_all(
    sparse_threshold: int = 80,
    rich_threshold: int = 150,
    problems_only: bool = False,
    output_file: str | None = None,
) -> None:
    from rag import validate_llamaparse_vs_pymupdf

    llama_dir  = os.path.join(BACKEND_DIR, "llamacache")
    pymupdf_dir = os.path.join(BACKEND_DIR, "pymupdfcache")

    if not os.path.exists(llama_dir):
        print(f"[錯誤] llamacache 目錄不存在：{llama_dir}")
        return

    # Collect all doc IDs that have a llamaparse .md cache
    llama_files = sorted(
        f for f in os.listdir(llama_dir) if f.endswith(".md")
    )
    doc_ids = []
    for fname in llama_files:
        try:
            doc_ids.append(int(fname[:-3]))
        except ValueError:
            pass

    if not doc_ids:
        print("找不到任何 llamaparse 快取檔（.md）")
        return

    lines = []
    _enc = sys.stdout.encoding or "utf-8"

    def out(text: str = "") -> None:
        lines.append(text)
        try:
            print(text)
        except UnicodeEncodeError:
            print(text.encode(_enc, errors="replace").decode(_enc, errors="replace"))

    out(f"{'=' * 72}")
    out(f"LlamaParse 缺頁掃描報告")
    out(f"  llamacache 目錄 : {llama_dir}")
    out(f"  pymupdfcache 目錄: {pymupdf_dir}")
    out(f"  判定門檻 : llamaparse < {sparse_threshold} chars 且 pymupdf > {rich_threshold} chars")
    out(f"  待掃描文件 : {len(doc_ids)} 份")
    out(f"{'=' * 72}")
    out()

    problem_ids: list[int] = []
    ok_ids: list[int] = []

    for doc_id in doc_ids:
        llama_path   = os.path.join(llama_dir,  f"{doc_id}.md")
        pymupdf_path = os.path.join(pymupdf_dir, f"{doc_id}.md")

        llama_docs  = _load_md_cache(llama_path,  "llamaparse")
        pymupdf_docs = _load_md_cache(pymupdf_path, "pymupdf4llm")

        if not pymupdf_docs:
            # No pymupdf cache → skip comparison
            if not problems_only:
                out(f"  [{doc_id:>5}] SKIP  (pymupdf4llm 快取不存在，無法比對)")
            continue

        issues = validate_llamaparse_vs_pymupdf(
            llama_docs, pymupdf_docs,
            sparse_threshold=sparse_threshold,
            rich_threshold=rich_threshold,
        )

        if issues:
            problem_ids.append(doc_id)
            errors = [i for i in issues if i["severity"] == "error"]
            warns  = [i for i in issues if i["severity"] == "warn"]
            tag = "[ERR]" if errors else "[WARN]"
            out(f"  [{doc_id:>5}] {tag}  {len(issues)} 頁有問題"
                f"（ERR={len(errors)}, WARN={len(warns)}）"
                f"  llama={len(llama_docs)}p  pymupdf={len(pymupdf_docs)}p")
            for issue in issues:
                severity_tag = "[ERR] " if issue["severity"] == "error" else "[WARN]"
                preview = issue["llama_preview"][:60] if issue["llama_preview"] else "(空白)"
                out(f"          p.{issue['page']:>3}  llama={issue['llama_chars']:>4}c  "
                    f"pymupdf={issue['pymupdf_chars']:>5}c  |  {preview}")
        else:
            ok_ids.append(doc_id)
            if not problems_only:
                out(f"  [{doc_id:>5}] OK    llama={len(llama_docs)}p  pymupdf={len(pymupdf_docs)}p")

    out()
    out(f"{'─' * 72}")
    out(f"掃描完成")
    out(f"  正常 (OK)   : {len(ok_ids)} 份")
    out(f"  有問題      : {len(problem_ids)} 份")

    if problem_ids:
        out()
        out("需要重新執行 LlamaParse 的文件編號：")
        out("  " + ", ".join(str(i) for i in problem_ids))
        out()
        out("（在前端管理頁面找到以上編號的文件，點「解析」→ LlamaParse 重新解析）")

    if output_file:
        try:
            with open(output_file, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
            print(f"\n[完成] 報告已寫入 {output_file}")
        except Exception as e:
            print(f"[錯誤] 無法寫入 {output_file}: {e}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="掃描 llamaparse 快取是否有缺頁（對比 pymupdf4llm）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--output", metavar="PATH",
        help="將報告寫入指定 UTF-8 檔案",
    )
    parser.add_argument(
        "--problems-only", action="store_true",
        help="只顯示有問題的文件，隱藏 OK 的",
    )
    parser.add_argument(
        "--sparse", type=int, default=80, metavar="N",
        help="llamaparse 低於此字元數視為可疑（預設 80）",
    )
    parser.add_argument(
        "--rich", type=int, default=150, metavar="N",
        help="pymupdf 高於此字元數才納入比對（預設 150）",
    )
    args = parser.parse_args()

    scan_all(
        sparse_threshold=args.sparse,
        rich_threshold=args.rich,
        problems_only=args.problems_only,
        output_file=args.output,
    )


if __name__ == "__main__":
    main()
