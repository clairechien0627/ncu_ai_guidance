"""
從 pdf_abstracts.json 把 abstract_raw 匯入資料庫的 abstract_text 欄位。
只更新尚未手動編輯（abstract_edited=False）且 abstract_text 為空的文件。

執行：
    python scripts/import_abstracts.py           # 預覽（dry-run）
    python scripts/import_abstracts.py --apply   # 實際寫入
"""

import json
import os
import sys
import argparse

SCRIPT_DIR  = os.path.dirname(os.path.abspath(__file__))
BACKEND_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, BACKEND_DIR)

from dotenv import load_dotenv
load_dotenv(os.path.join(BACKEND_DIR, ".env"))

ABSTRACTS_JSON = os.path.join(SCRIPT_DIR, "pdf_abstracts.json")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="實際寫入資料庫（預設 dry-run）")
    args = parser.parse_args()

    with open(ABSTRACTS_JSON, encoding="utf-8") as f:
        records = json.load(f)

    abstract_map: dict[str, str] = {}
    for r in records:
        if not r.get("has_abstract") or not r.get("abstract_raw"):
            continue
        pdf_path = r.get("pdfPath", "")
        filename = os.path.basename(pdf_path)
        if filename:
            abstract_map[filename] = r["abstract_raw"].strip()

    print(f"JSON 中有摘要的 PDF：{len(abstract_map)} 筆")

    from db import SessionLocal, Document
    db = SessionLocal()
    try:
        docs = db.query(Document).filter(
            Document.status == "ready",
            Document.abstract_edited == False,  # noqa: E712
            Document.abstract_text == None,      # noqa: E711
        ).all()

        print(f"資料庫中需要填入摘要的文件：{len(docs)} 筆")
        print()

        matched = 0
        unmatched = []

        for doc in docs:
            abstract = abstract_map.get(doc.filename)
            if abstract:
                matched += 1
                if args.apply:
                    doc.abstract_text = abstract
                else:
                    preview = abstract[:80].replace("\n", " ")
                    print(f"  [預覽] {doc.filename[:50]}  →  {preview}...")
            else:
                unmatched.append(doc.filename)

        if args.apply:
            db.commit()
            print(f"完成！成功寫入 {matched} 筆摘要。")
        else:
            print(f"\n符合：{matched} 筆  |  無對應：{len(unmatched)} 筆")
            if unmatched:
                print("\n無對應的文件（可能需要手動填寫）：")
                for fn in unmatched[:20]:
                    print(f"  {fn}")
                if len(unmatched) > 20:
                    print(f"  ... 以及其他 {len(unmatched) - 20} 筆")
            print("\n加上 --apply 參數即可實際寫入。")
    finally:
        db.close()


if __name__ == "__main__":
    main()
