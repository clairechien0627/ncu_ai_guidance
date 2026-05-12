"""
PDF 解析品質比較腳本
用法:
  python scripts/test_llama.py <PDF路徑> [選項]

選項:
  --no-cache    刪掉 LlamaCloud cache，強制重新呼叫 API
  --compare     同時顯示本地 pymupdf4llm 結果
  --azure-di    同時顯示 Azure Document Intelligence 結果
  --all         以上全部

範例:
  python scripts/test_llama.py "D:/try/各系大專生計畫/物理學系/xxx.pdf" --all
"""
import sys, os, warnings
warnings.filterwarnings("ignore")

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND_DIR)

from dotenv import load_dotenv
load_dotenv(os.path.join(BACKEND_DIR, ".env"))


def _stats(docs) -> str:
    total = sum(len(d.page_content) for d in docs)
    cjk = sum(sum(1 for c in d.page_content if '\u4e00' <= c <= '\u9fff') for d in docs)
    ratio = cjk / total * 100 if total else 0
    return f"{len(docs)} 頁 | {total} 字元 | CJK {ratio:.1f}%"


def show_docs(docs, label: str, preview=500):
    print(f"\n{'█'*60}")
    print(f"  {label}")
    print(f"  {_stats(docs)}")
    print(f"{'█'*60}")
    for i, doc in enumerate(docs[:2]):
        text = doc.page_content
        print(f"\n--- 第 {i+1} 頁 ---")
        print(text[:preview])
        if len(text) > preview:
            print(f"... (省略 {len(text)-preview} 字元)")


def parse_local(pdf_path):
    try:
        import pymupdf4llm
        from langchain_core.documents import Document as D
        pages = pymupdf4llm.to_markdown(pdf_path, page_chunks=True)
        return [D(page_content=p.get("text","") if isinstance(p,dict) else str(p),
                  metadata={"page":i}) for i, p in enumerate(pages)]
    except Exception as e:
        print(f"[pymupdf4llm 失敗] {e}"); return []


def parse_azure_di(pdf_path):
    try:
        from azure.core.credentials import AzureKeyCredential
        from azure.ai.documentintelligence import DocumentIntelligenceClient
        from langchain_core.documents import Document as D
        from config import settings
        if not settings.azure_document_intelligence_endpoint or not settings.azure_document_intelligence_key:
            print("[Azure DI] AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT / KEY 未設定"); return []
        print("  [Azure DI 解析中...]")
        client = DocumentIntelligenceClient(
            endpoint=settings.azure_document_intelligence_endpoint,
            credential=AzureKeyCredential(settings.azure_document_intelligence_key),
        )
        with open(pdf_path, "rb") as f:
            poller = client.begin_analyze_document("prebuilt-layout", body=f, content_type="application/pdf", output_content_format="markdown")
        result = poller.result()
        md = result.content or ""
        import re
        pages = re.split(r'<!-- PageBreak -->', md)
        pages = [p.strip() for p in pages if p.strip()]
        if not pages:
            pages = [md]
        return [D(page_content=p, metadata={"page":i}) for i,p in enumerate(pages)]
    except Exception as e:
        print(f"[Azure DI 失敗] {e}"); return []


def parse_llamacloud(pdf_path, doc_id: int = 0):
    from rag import _parse_with_llamaparse
    return _parse_with_llamaparse(pdf_path, doc_id=doc_id)


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__); sys.exit(1)

    no_cache = "--no-cache" in args
    do_all   = "--all" in args
    do_local = "--compare" in args or do_all
    do_azure_di = "--azure-di" in args or do_all
    pdf_path = next((a for a in args if not a.startswith("--")), None)

    if not pdf_path or not os.path.exists(pdf_path):
        print(f"找不到檔案：{pdf_path}"); sys.exit(1)

    from rag import _llamaparse_cache_path
    from database import SessionLocal, Document as _Doc
    _db = SessionLocal()
    try:
        _doc = _db.query(_Doc).filter(_Doc.file_path == pdf_path).first()
        _doc_id = _doc.id if _doc else 0
    finally:
        _db.close()
    if not _doc_id:
        print("[警告] 找不到 DB 記錄，快取路徑可能不正確")
    cache_path = _llamaparse_cache_path(_doc_id)
    json_cache = cache_path[:-3] + ".json"

    if no_cache:
        for p in [cache_path, json_cache]:
            if os.path.exists(p):
                os.remove(p)
                print(f"[cache 刪除] {os.path.basename(p)}")
    elif os.path.exists(cache_path) or os.path.exists(json_cache):
        print("[cache] 使用既有 cache（加 --no-cache 強制重打 API）")

    print(f"\n目標：{os.path.basename(pdf_path)}\n")

    results = {}
    if do_local:
        results["本地 pymupdf4llm"] = parse_local(pdf_path)
    if do_azure_di:
        results["Azure Document Intelligence"] = parse_azure_di(pdf_path)
    results["LlamaCloud (LlamaParse)"] = parse_llamacloud(pdf_path, doc_id=_doc_id)

    for label, docs in results.items():
        if docs:
            show_docs(docs, label)
        else:
            print(f"\n[{label}] 無結果")

    if len(results) > 1:
        print(f"\n{'='*60}")
        print("  對比摘要")
        print(f"{'='*60}")
        for label, docs in results.items():
            if docs:
                print(f"  {label:<30} {_stats(docs)}")


if __name__ == "__main__":
    main()
