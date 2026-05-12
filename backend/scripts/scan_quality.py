"""
Scan all documents in PostgreSQL, run quality checks on their PDFs,
and update the quality_issue field — without touching Qdrant.

Usage:
    python scripts/scan_quality.py [--dry-run] [--limit N]

Options:
    --dry-run   Print results only, do not write to DB
    --limit N   Only scan the first N documents (for testing)
"""

import argparse
import os
import sys

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND_DIR)

from dotenv import load_dotenv
load_dotenv(os.path.join(BACKEND_DIR, ".env"))

from langchain_community.document_loaders import PyMuPDFLoader

from database import SessionLocal, Document
from rag import (
    _clean_text,
    _detect_language,
    _is_cover_page,
    _is_toc_page,
    _check_quality,
    _parse_with_pymupdf4llm,
    _extract_headings_from_markdown,
    _classify_candidate_text,
)


def _assess_quality(file_path: str, doc_id: int = 0) -> str | None:
    """Run quality assessment on a PDF file. Returns quality_issue or None."""
    docs = _parse_with_pymupdf4llm(file_path, doc_id=doc_id)
    if not docs:
        loader = PyMuPDFLoader(file_path)
        docs = loader.load()

    if not docs:
        return "scanned"

    lang = _detect_language(docs)
    for doc in docs:
        doc.page_content = _clean_text(doc.page_content, lang)

    kept = [
        d for d in docs
        if d.page_content
        and not _is_cover_page(d.page_content)
        and not _is_toc_page(d.page_content, lang)
    ]

    if not kept:
        return "scanned"

    quality_issue = _check_quality(kept)

    if quality_issue is None and any(d.metadata.get("pymupdf4llm") for d in kept):
        md_headings = _extract_headings_from_markdown(kept)
        if len(md_headings) >= 3:
            heading_text = " ".join(h["text"] for h in md_headings)
            total_h = sum(1 for c in heading_text if not c.isspace())
            if total_h > 0 and heading_text.count('\ufffd') / total_h > 0.25:
                quality_issue = "garbled"

    return quality_issue


def main():
    parser = argparse.ArgumentParser(description="Scan PDF quality without re-indexing")
    parser.add_argument("--dry-run", action="store_true", help="Print only, do not save")
    parser.add_argument("--limit", type=int, default=None, help="Max documents to scan")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        query = db.query(Document).filter(Document.status == "ready")
        if args.limit:
            query = query.limit(args.limit)
        documents = query.all()
    finally:
        db.close()

    print(f"掃描 {len(documents)} 份文件{'（dry-run）' if args.dry_run else ''}\n")

    counts: dict[str, int] = {}
    errors = []

    for i, doc in enumerate(documents, 1):
        print(f"[{i:>3}/{len(documents)}] {doc.filename[:55]:<55}", end=" ", flush=True)

        if not os.path.exists(doc.file_path):
            print("SKIP (檔案不存在)")
            errors.append((doc.id, doc.filename, "file missing"))
            continue

        try:
            quality_issue = _assess_quality(doc.file_path, doc_id=doc.id)
            label = quality_issue or "ok"
            counts[label] = counts.get(label, 0) + 1
            print(f"→ {label}")

            if not args.dry_run:
                db2 = SessionLocal()
                try:
                    db2.query(Document).filter(Document.id == doc.id).update(
                        {"quality_issue": quality_issue}
                    )
                    db2.commit()
                finally:
                    db2.close()

        except Exception as exc:
            print(f"ERROR: {exc}")
            errors.append((doc.id, doc.filename, str(exc)))

    print("\n" + "─" * 60)
    print("結果統計：")
    for label, cnt in sorted(counts.items(), key=lambda x: -x[1]):
        print(f"  {label:<14} {cnt:>4} 份")
    if errors:
        print(f"\n  錯誤/跳過   {len(errors):>4} 份")
        for doc_id, fname, reason in errors:
            print(f"    id={doc_id} {fname[:40]} ({reason})")
    print(f"\n  合計        {sum(counts.values()):>4} 份掃描完成")


if __name__ == "__main__":
    main()
