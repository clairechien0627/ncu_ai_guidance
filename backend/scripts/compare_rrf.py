"""
Compare equal-weight RRF vs weighted RRF (dense 2 : sparse 1) for hybrid search.

Usage:
    python scripts/compare_rrf.py --doc-ids 1 2 --queries "研究方法" "研究限制"
    python scripts/compare_rrf.py --doc-ids 5 --queries "資料蒐集方式" "研究架構"

Output shows, for each query:
  - Candidate pool diff  (after fusion, before reranker)
  - Final result diff    (after reranker)
"""

import argparse
import hashlib
import os
import sys
import textwrap

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND_DIR)

from dotenv import load_dotenv
load_dotenv(os.path.join(BACKEND_DIR, ".env"))

from qdrant_client.models import Filter, FieldCondition, MatchAny, RrfQuery, Rrf

from rag.store import (
    RETRIEVAL_K, RERANK_TOP_N, RERANK_MAX,
    get_vectorstore, get_dense_vectorstore, get_reranker,
    get_document_language,
)
from rag.cleaning import _is_cover_page, _is_references_page, _is_table_or_formula_heavy


_EQUAL_RRF = None
_WEIGHTED_RRF = RrfQuery(rrf=Rrf(weights=[2.0, 1.0]))

SEP = "─" * 72


def _key(content: str) -> str:
    return hashlib.md5(content.encode("utf-8", errors="replace")).hexdigest()


def _preview(text: str, width: int = 120) -> str:
    text = " ".join(text.split())
    return textwrap.shorten(text, width=width, placeholder="…")


def _fetch_candidates(queries, doc_ids, fusion):
    """Return deduplicated candidates before reranking."""
    effective_lang = get_document_language(doc_ids)
    if effective_lang == "en":
        vectorstore = get_dense_vectorstore()
        active_fusion = None
    else:
        vectorstore = get_vectorstore()
        active_fusion = fusion

    qdrant_filter = None
    if doc_ids:
        qdrant_filter = Filter(must=[FieldCondition(
            key="metadata.document_id",
            match=MatchAny(any=[str(d) for d in doc_ids]),
        )])

    seen: set[str] = set()
    results = []
    for q in queries:
        hits = vectorstore.similarity_search(
            q, k=RETRIEVAL_K, filter=qdrant_filter, hybrid_fusion=active_fusion
        )
        for doc in hits:
            sec = doc.metadata.get("section", "")
            if sec in ("references", "參考文獻"):
                continue
            if _is_cover_page(doc.page_content) or _is_references_page(doc.page_content):
                continue
            k = _key(doc.page_content)
            if k not in seen:
                seen.add(k)
                results.append(doc)
    return results


def _rerank(candidates, queries):
    """Return reranked docs with scores."""
    if not candidates:
        return {}
    reranker = get_reranker()
    best_score: dict[str, float] = {}
    best_doc = {}
    for q in queries[:3]:
        for doc in reranker.compress_documents(candidates, q):
            k = _key(doc.page_content)
            score = doc.metadata.get("relevance_score", 0.0)
            if score > best_score.get(k, -1):
                best_score[k] = score
                best_doc[k] = doc

    is_heavy = {k: _is_table_or_formula_heavy(d.page_content) for k, d in best_doc.items()}
    ranked = sorted(best_doc.values(), key=lambda d: (is_heavy.get(_key(d.page_content), False), -best_score[_key(d.page_content)]))
    top = ranked[:min(RERANK_TOP_N, RERANK_MAX)]
    return {_key(d.page_content): (d, best_score[_key(d.page_content)]) for d in top}


def _fmt_doc(doc, score=None) -> str:
    fn = doc.metadata.get("filename", "?")
    pg = doc.metadata.get("page", "?")
    sec = doc.metadata.get("section", "")
    score_str = f"  score={score:.3f}" if score is not None else ""
    preview = _preview(doc.page_content)
    return f"  [{fn} p.{int(pg)+1 if pg != '?' else '?'} §{sec}]{score_str}\n    {preview}"


def compare_query(query: str, doc_ids: list[int]):
    print(f"\n{'═' * 72}")
    print(f"Query: 「{query}」")
    print('═' * 72)

    cands_eq = _fetch_candidates([query], doc_ids, _EQUAL_RRF)
    cands_wt = _fetch_candidates([query], doc_ids, _WEIGHTED_RRF)

    keys_eq = {_key(d.page_content): d for d in cands_eq}
    keys_wt = {_key(d.page_content): d for d in cands_wt}

    only_eq = {k: d for k, d in keys_eq.items() if k not in keys_wt}
    only_wt = {k: d for k, d in keys_wt.items() if k not in keys_eq}
    common = len(keys_eq) - len(only_eq)

    print(f"\n[候選池]  equal={len(keys_eq)}  weighted={len(keys_wt)}  共同={common}")

    if only_wt:
        print(f"\n  ++ weighted 新增 {len(only_wt)} 個（equal 沒有）:")
        for d in only_wt.values():
            print(_fmt_doc(d))
    if only_eq:
        print(f"\n  -- weighted 移除 {len(only_eq)} 個（equal 才有）:")
        for d in only_eq.values():
            print(_fmt_doc(d))
    if not only_wt and not only_eq:
        print("  候選池完全相同，fusion 權重對此 query 無影響。")

    # ── Rerank comparison ──────────────────────────────────────────────────────
    final_eq = _rerank(cands_eq, [query])
    final_wt = _rerank(cands_wt, [query])

    keys_feq = set(final_eq)
    keys_fwt = set(final_wt)
    only_feq = keys_feq - keys_fwt
    only_fwt = keys_fwt - keys_feq

    print(f"\n[最終結果] equal={len(final_eq)}  weighted={len(final_wt)}  共同={len(keys_feq & keys_fwt)}")

    if only_fwt:
        print(f"\n  ++ weighted 最終結果新增:")
        for k in only_fwt:
            d, s = final_wt[k]
            print(_fmt_doc(d, s))
    if only_feq:
        print(f"\n  -- weighted 最終結果移除:")
        for k in only_feq:
            d, s = final_eq[k]
            print(_fmt_doc(d, s))
    if not only_fwt and not only_feq:
        print("  最終結果完全相同。")


def main():
    parser = argparse.ArgumentParser(description="Compare equal vs weighted RRF")
    parser.add_argument("--doc-ids", nargs="+", type=int, required=True, help="Document IDs to search in")
    parser.add_argument("--queries", nargs="+", required=True, help="Queries to test")
    args = parser.parse_args()

    print(f"Doc IDs : {args.doc_ids}")
    print(f"Queries : {args.queries}")
    print(f"Settings: RETRIEVAL_K={RETRIEVAL_K}  RERANK_TOP_N={RERANK_TOP_N}  weighted=[2.0, 1.0]")

    for q in args.queries:
        compare_query(q, args.doc_ids)

    print(f"\n{'═' * 72}")
    print("完成。「++」代表 weighted 新增、「--」代表 weighted 移除。")
    print("若新增的 chunk 比移除的更相關 → 改動正向；反之負向。")


if __name__ == "__main__":
    main()
