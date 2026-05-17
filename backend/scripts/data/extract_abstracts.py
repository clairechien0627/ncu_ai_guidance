"""
extract_abstracts.py

從已上傳文件（資料庫 Document 表）批次抽取 PDF 摘要與關鍵字，
輸出至 scripts/pdf_abstracts.json，供 import_abstracts.py 匯入資料庫。

支援本地存儲（settings.upload_dir）與 Azure Blob Storage（自動偵測）。

執行（在 backend/ 目錄下）：
    python scripts/extract_abstracts.py              # 全部（只產生 JSON）
    python scripts/extract_abstracts.py --limit 10  # 只跑前 10 筆
    python scripts/extract_abstracts.py --show 3    # 印出前 3 筆摘要原文
    python scripts/extract_abstracts.py --apply     # 同時直接寫入 DB abstract_text

依賴套件：
    pip install pymupdf tiktoken
"""

import os
import re
import json
import argparse
import sys
import tempfile

SCRIPT_DIR  = os.path.dirname(os.path.abspath(__file__))
BACKEND_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, BACKEND_DIR)

from dotenv import load_dotenv
load_dotenv(os.path.join(BACKEND_DIR, ".env"))

OUTPUT_JSON = os.path.join(SCRIPT_DIR, "pdf_abstracts.json")

# 摘要標題關鍵字（優先順序由前到後）
ABSTRACT_KEYWORDS = [
    '摘要', 'Abstract', 'ABSTRACT', '中文摘要', '英文摘要', '研究摘要',
    '中英文摘要', '摘 要', '大綱', '概要', 'Summary', 'SUMMARY',
    '題目敘述',
    '前言',
]

# 關鍵字標題關鍵字
KEYWORD_KEYWORDS = ['關鍵字', '關鍵詞', 'Keywords', 'Key words', 'KEYWORDS', '索引詞']

MAX_ABSTRACT_CHARS = 2000


# ─────────────────────────────────────────
# Token 計算
# ─────────────────────────────────────────
try:
    import tiktoken
    _enc = tiktoken.get_encoding("cl100k_base")
    def count_tokens(text: str) -> int:
        return len(_enc.encode(text))
except ImportError:
    def count_tokens(text: str) -> int:
        return int(len(text) / 1.5)


def parse_keywords(text: str) -> list[str]:
    kw_pattern = '|'.join(re.escape(k) for k in KEYWORD_KEYWORDS)
    cleaned = re.sub(rf'^({kw_pattern})[：:\s]*', '', text.strip(), flags=re.IGNORECASE)
    cleaned = re.sub(r'\s+[IVXivx]+\s*$', '', cleaned).strip()
    parts = re.split(r'[、，,；;　\s]+', cleaned)
    keywords = [re.sub(r'[。，、；：！？\.\s]+$', '', p.strip())
                for p in parts if 2 <= len(p.strip()) <= 25]
    return [k for k in keywords if len(k) >= 2]


def is_toc_line(i: int, all_texts: list) -> bool:
    for j in range(1, 4):
        if i + j < len(all_texts):
            nt = all_texts[i + j]['text']
            if nt.count('.') > 3 or '...' in nt or '…' in nt:
                return True
    return False


def extract_abstract_and_keywords(pdf_path: str) -> dict:
    try:
        import fitz
    except ImportError:
        raise ImportError("請先安裝 PyMuPDF：pip install pymupdf")

    result = {
        'abstract_raw':     '',
        'abstract_tokens':  0,
        'abstract_heading': '',
        'keywords':         [],
        'keyword_heading':  '',
        'has_abstract':     False,
        'has_keywords':     False,
    }

    try:
        doc = fitz.open(pdf_path)
    except Exception:
        return result

    font_counts: dict = {}
    for page in doc:
        try:
            for b in page.get_text("dict")["blocks"]:
                if b.get('type') == 0:
                    for l in b.get("lines", []):
                        for s in l.get("spans", []):
                            sz = round(s['size'], 2)
                            font_counts[sz] = font_counts.get(sz, 0) + len(s['text'].strip())
        except Exception:
            pass

    if not font_counts:
        return result

    body_size = max(font_counts, key=font_counts.get)

    all_texts = []
    for page_num in range(min(len(doc), 15)):
        page = doc[page_num]
        try:
            for b in page.get_text("dict")["blocks"]:
                if b.get('type') == 0:
                    for l in b.get("lines", []):
                        for s in l.get("spans", []):
                            t = s['text'].replace('\n', '')
                            if t.strip():
                                all_texts.append({
                                    'page':  page_num + 1,
                                    'size':  round(s['size'], 2),
                                    'text':  t,
                                    'font':  s['font'],
                                    'flags': s['flags'],
                                })
                    all_texts.append({'page': page_num + 1, 'size': body_size,
                                      'text': '\n', 'font': 'end_of_block', 'flags': 0})
        except Exception:
            pass

    # 合併相鄰短 span
    merged = []
    i = 0
    while i < len(all_texts):
        span = all_texts[i]
        if span['font'] == 'end_of_block':
            merged.append(span)
            i += 1
            continue
        if len(span['text'].strip()) <= 3:
            combined_text  = span['text']
            combined_size  = span['size']
            combined_font  = span['font']
            combined_flags = span['flags']
            j = i + 1
            while j < len(all_texts):
                nxt = all_texts[j]
                if nxt['font'] == 'end_of_block':
                    break
                if nxt['page'] != span['page']:
                    break
                if len(nxt['text'].strip()) <= 3:
                    combined_text += nxt['text']
                    j += 1
                    if len(combined_text.strip()) > 6:
                        break
                else:
                    break
            merged.append({'page': span['page'], 'size': combined_size,
                           'text': combined_text, 'font': combined_font,
                           'flags': combined_flags})
            i = j
        else:
            merged.append(span)
            i += 1
    all_texts = merged
    n = len(all_texts)

    kw_zh_markers = ['關鍵字', '關鍵詞', '中文關鍵詞', '中文關鍵字']
    kw_en_markers = ['Keywords', 'KEYWORDS', 'Key words']
    keyword_hits = []

    for i, span in enumerate(all_texts):
        if span['font'] == 'end_of_block':
            continue
        stripped = span['text'].strip()
        lang = None
        for kw in kw_zh_markers:
            if stripped.startswith(kw) or (kw + '：') in stripped or (kw + ':') in stripped:
                lang = 'zh'
                break
        if lang is None:
            for kw in kw_en_markers:
                if stripped.startswith(kw) or (kw + ':') in stripped or (kw + '：') in stripped:
                    lang = 'en'
                    break
        if lang:
            combined = stripped
            for j in range(1, 4):
                if i + j < n and all_texts[i + j]['font'] != 'end_of_block':
                    next_text = all_texts[i + j]['text'].strip()
                    if re.search(r'[、，,]', next_text) and len(next_text) < 80:
                        combined += next_text
                    else:
                        break
                else:
                    break
            keyword_hits.append((i, lang, combined))

    zh_hits = [(i, t) for i, lang, t in keyword_hits if lang == 'zh']
    en_hits = [(i, t) for i, lang, t in keyword_hits if lang == 'en']

    chosen_kw_idx  = -1
    chosen_kw_text = ''
    if zh_hits:
        chosen_kw_idx, chosen_kw_text = zh_hits[0]
    elif en_hits:
        chosen_kw_idx, chosen_kw_text = en_hits[0]

    if chosen_kw_text:
        result['keyword_heading'] = chosen_kw_text[:80]
        result['keywords']        = parse_keywords(chosen_kw_text)
        result['has_keywords']    = bool(result['keywords'])

    zh_abstract_kws = [k for k in ABSTRACT_KEYWORDS if not k.isascii()]
    en_abstract_kws = [k for k in ABSTRACT_KEYWORDS if k.isascii()]

    def find_abstract(kw_list: list) -> tuple[int, str]:
        import unicodedata
        for i, span in enumerate(all_texts):
            if span['font'] == 'end_of_block':
                continue
            stripped   = span['text'].strip()
            normalized = unicodedata.normalize('NFKC', stripped).replace(' ', '').replace('　', '')
            for kw in kw_list:
                kw_norm = unicodedata.normalize('NFKC', kw).replace(' ', '')
                if kw_norm in normalized and len(normalized) <= 30:
                    if not is_toc_line(i, all_texts):
                        return i, stripped
        return -1, ''

    abs_idx, abs_heading = find_abstract(zh_abstract_kws)
    if abs_idx == -1:
        abs_idx, abs_heading = find_abstract(en_abstract_kws)

    if abs_idx >= 0:
        result['abstract_heading'] = abs_heading
        abstract_parts = []
        stop_headings  = set(zh_abstract_kws + en_abstract_kws + [
            '目錄', 'Table of Contents', '致謝', 'Acknowledgement', '目次'])

        for i in range(abs_idx + 1, n):
            span    = all_texts[i]
            text    = span['text']
            font    = span['font']
            size    = span['size']
            flags   = span['flags']
            stripped = text.strip()

            if font == 'end_of_block':
                abstract_parts.append('\n')
                continue
            if i == chosen_kw_idx or any(stripped.startswith(kw) for kw in
                                         kw_zh_markers + kw_en_markers):
                break

            font_lower = font.lower()
            is_bold    = bool(flags & 16) or any(k in font_lower for k in ('bold', 'heavy', 'black'))
            is_large   = size > body_size + 0.5
            if (is_bold or is_large) and len(stripped) <= 20:
                if stripped in stop_headings or sum(len(p) for p in abstract_parts) > 80:
                    break

            abstract_parts.append(text)
            if sum(len(p) for p in abstract_parts) >= MAX_ABSTRACT_CHARS:
                break

        abstract_text = ''.join(abstract_parts).strip()
        for kw in kw_zh_markers + kw_en_markers:
            abstract_text = re.sub(rf'({kw})[：:\s].*$', '', abstract_text,
                                   flags=re.MULTILINE).strip()

        result['abstract_raw']    = abstract_text
        result['abstract_tokens'] = count_tokens(abstract_text) if abstract_text else 0
        result['has_abstract']    = bool(abstract_text)

    return result


# ─────────────────────────────────────────
# 取得 PDF 本地路徑（支援 Azure Blob）
# ─────────────────────────────────────────
def get_pdf_local_path(filename: str) -> tuple[str, bool]:
    """
    回傳 (local_path, is_temp)。
    本地存儲直接回傳路徑；Azure Blob 下載到暫存檔，is_temp=True 需呼叫端刪除。
    """
    from config import settings
    from services.storage_service import _USE_BLOB

    if _USE_BLOB:
        from services.storage_service import download_blob_to_tmp
        return download_blob_to_tmp(filename), True
    else:
        return os.path.join(settings.upload_dir, filename), False


# ─────────────────────────────────────────
# 統計報告
# ─────────────────────────────────────────
def print_report(records: list):
    total        = len(records)
    has_abstract = sum(1 for r in records if r['has_abstract'])
    has_keywords = sum(1 for r in records if r['has_keywords'])
    both_missing = sum(1 for r in records if not r['has_abstract'] and not r['has_keywords'])
    tokens       = [r['abstract_tokens'] for r in records if r['has_abstract']]
    kw_counts    = [len(r['keywords']) for r in records if r['has_keywords']]

    print("\n" + "=" * 60)
    print(f"  摘要抽取統計報告  (共 {total} 份 PDF)")
    print("=" * 60)
    print(f"  找到摘要：{has_abstract} 份 ({has_abstract/total*100:.1f}%)")
    print(f"  找到關鍵字：{has_keywords} 份 ({has_keywords/total*100:.1f}%)")
    print(f"  兩者皆無：{both_missing} 份")

    if tokens:
        print(f"\n  [摘要 tokens]")
        print(f"    平均：{int(sum(tokens)/len(tokens))} ｜ 最小：{min(tokens)} ｜ 最大：{max(tokens)}")
        over_800  = sum(1 for t in tokens if t > 800)
        over_1500 = sum(1 for t in tokens if t > 1500)
        if over_800:  print(f"    超過 800 tokens：{over_800} 份")
        if over_1500: print(f"    超過 1500 tokens：{over_1500} 份")

    if kw_counts:
        print(f"\n  [關鍵字數量]")
        print(f"    平均：{sum(kw_counts)/len(kw_counts):.1f} 個 ｜ 最多：{max(kw_counts)} 個")

    no_abstract = [r for r in records if not r['has_abstract']]
    if no_abstract:
        print(f"\n  ⚠ 找不到摘要（共 {len(no_abstract)} 份）：")
        for r in no_abstract[:15]:
            print(f"    [doc_id={r['id']}] {r['filename'][:50]}")
        if len(no_abstract) > 15:
            print(f"    ... 以及其他 {len(no_abstract) - 15} 份")

    print("=" * 60)


# ─────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--limit', type=int, default=0,
                        help='限制處理筆數（0 = 全部）')
    parser.add_argument('--show',  type=int, default=0,
                        help='印出前 N 筆的摘要原文')
    parser.add_argument('--apply', action='store_true',
                        help='直接將摘要寫入資料庫 abstract_text（需 abstract_text 為空且未手動編輯）')
    args = parser.parse_args()

    from db import SessionLocal, Document

    db = SessionLocal()
    try:
        query = db.query(Document).filter(Document.status == "ready")
        docs  = query.all()
    finally:
        db.close()

    print(f"資料庫中 ready 文件：{len(docs)} 份")

    # 斷點續跑：讀已有記錄
    if os.path.exists(OUTPUT_JSON):
        with open(OUTPUT_JSON, 'r', encoding='utf-8') as f:
            records = json.load(f)
        done_ids = {r['id'] for r in records}
        print(f"發現已有 {len(records)} 筆，繼續補跑...")
    else:
        records  = []
        done_ids = set()

    limit     = args.limit if args.limit > 0 else len(docs)
    shown     = 0
    processed = 0

    for doc in docs:
        if processed >= limit:
            break
        if doc.id in done_ids:
            continue

        pdf_path, is_temp = get_pdf_local_path(doc.filename)

        if not os.path.exists(pdf_path):
            print(f"[doc_id={doc.id}] 找不到 PDF，跳過：{doc.filename}")
            if is_temp:
                try: os.unlink(pdf_path)
                except Exception: pass
            continue

        data = extract_abstract_and_keywords(pdf_path)

        if is_temp:
            try: os.unlink(pdf_path)
            except Exception: pass

        record = {
            'id':       doc.id,
            'filename': doc.filename,
            **data,
        }
        records.append(record)
        done_ids.add(doc.id)
        processed += 1

        ab_status = f"摘要={data['abstract_tokens']}tok" if data['has_abstract'] else "摘要=未找到"
        kw_status = f"關鍵字={len(data['keywords'])}個"  if data['has_keywords'] else "關鍵字=未找到"
        print(f"[doc_id={doc.id}] {doc.filename[:45]}  {ab_status} | {kw_status}")

        if args.show and shown < args.show and data['has_abstract']:
            print(f"  ── 摘要原文 ──")
            print(f"  {data['abstract_raw'][:400]}")
            if data['keywords']:
                print(f"  ── 關鍵字 ──  {data['keywords']}")
            print()
            shown += 1

        with open(OUTPUT_JSON, 'w', encoding='utf-8') as f:
            json.dump(records, f, ensure_ascii=False, indent=2)

    print(f"\n完成！共 {len(records)} 筆已存入：\n  {OUTPUT_JSON}")
    print_report(records)

    # --apply：直接寫入 DB
    if args.apply:
        db = SessionLocal()
        try:
            written = 0
            for r in records:
                if not r.get('has_abstract') or not r.get('abstract_raw'):
                    continue
                doc = db.query(Document).filter(
                    Document.id == r['id'],
                    Document.abstract_edited == False,  # noqa: E712
                    Document.abstract_text == None,     # noqa: E711
                ).first()
                if doc:
                    doc.abstract_text = r['abstract_raw'].strip()
                    written += 1
            db.commit()
            print(f"\n[--apply] 寫入 DB 完成：{written} 筆。")
        finally:
            db.close()
    else:
        print("\n加上 --apply 可直接寫入資料庫，或執行 python scripts/import_abstracts.py --apply")


if __name__ == '__main__':
    main()
