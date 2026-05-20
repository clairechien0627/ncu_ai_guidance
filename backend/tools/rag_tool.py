"""LangChain @tool adapters for document search and retrieval.

Business logic lives in tools.search_core.  This module is the thin adapter
layer that wraps core functions as LangChain tools and exposes the TOOLS list
consumed by runner.py.
"""
import json

from langchain.tools import ToolRuntime
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool
from langfuse import observe

from tools.search_core import (
    AgentContext,
    SearchInput,
    _emit_stage_sync,
    _get_query_expander_llm,
    expand_queries,
    _hyde_expand,
    run_search_report,
    set_query_expander_llm,
)
from rag import aget_document_language as _aget_document_language

_VERIFY_CLAIM_SYSTEM = (
    "你是文件事實核查員。給定一個聲明和相關文件片段，判斷文件原文是否支撐該聲明。\n"
    "只依據文件原文判斷，不補充外部知識。\n\n"
    "輸出純 JSON（不加 markdown）：\n"
    '{"verdict": "SUPPORTED|PARTIAL|UNSUPPORTED", '
    '"confidence": 0.0-1.0, '
    '"evidence": ["直接引文或片段（繁體中文）"], '
    '"explanation": "一兩句說明（繁體中文）"}'
)

_SECTION_ALIASES: dict[str, list[str]] = {
    "abstract":      ["abstract", "摘要", "概要"],
    "introduction":  ["introduction", "緒論", "引言", "前言", "研究背景", "background"],
    "related_work":  ["related_work", "文獻回顧", "相關研究", "文獻探討", "related work"],
    "methods":       ["methods", "method", "研究方法", "實驗方法", "methodology", "方法"],
    "results":       ["results", "result", "研究成果", "實驗結果", "findings", "成果", "研究結果"],
    "conclusion":    ["conclusion", "結論", "討論", "summary", "未來工作"],
}


def _resolve_section(raw: str) -> str:
    lower = raw.lower().strip()
    for canonical, aliases in _SECTION_ALIASES.items():
        if lower in [a.lower() for a in aliases]:
            return canonical
        if any(lower in a.lower() or a.lower() in lower for a in aliases):
            return canonical
    return lower


# ── Tools ─────────────────────────────────────────────────────────────────────

@tool(args_schema=SearchInput)
@observe(as_type="tool")
async def search_report(
    query: str,
    runtime: ToolRuntime[AgentContext],
    sub_queries: list[str] | None = None,
    display_intent: str = "",
    keyword_query: str = "",
    semantic_query: str = "",
    section_terms: list[str] | None = None,
    use_hyde: bool = False,
) -> str:
    """
    Search uploaded documents for evidence about ONE focused topic.

    Use for document-specific questions about motivation, methods, experiments,
    results, limitations, definitions, sections, architectures, and frameworks.
    """
    return await run_search_report(
        query=query,
        ctx=runtime.context,
        sub_queries=sub_queries,
        display_intent=display_intent,
        keyword_query=keyword_query,
        semantic_query=semantic_query,
        section_terms=section_terms,
        use_hyde=use_hyde,
    )


@tool
@observe(as_type="tool")
async def detect_document_language(runtime: ToolRuntime[AgentContext]) -> str:
    """Detect primary language of uploaded documents."""
    ctx = runtime.context
    _emit_stage_sync(ctx.on_stage, "偵測文件語言")
    lang = await _aget_document_language(ctx.document_ids or None)
    return json.dumps({"language": lang}, ensure_ascii=False)


@tool
@observe(as_type="tool")
def list_documents(runtime: ToolRuntime[AgentContext]) -> str:
    """List uploaded ready documents in current conversation."""
    from db import Document as DocModel, db_session

    ctx = runtime.context
    with db_session() as db:
        query = db.query(DocModel).filter(DocModel.status == "ready")
        if ctx.document_ids:
            query = query.filter(DocModel.id.in_(ctx.document_ids))
        docs = query.order_by(DocModel.created_at.desc()).all()
        return json.dumps(
            [{"id": doc.id, "filename": doc.filename} for doc in docs],
            ensure_ascii=False,
        )


@tool
@observe(as_type="tool")
def get_document_metadata(runtime: ToolRuntime[AgentContext]) -> str:
    """
    Get detailed metadata for the selected documents.

    Returns filename, upload date, category, tags, quality notes and a short
    abstract preview. Use this when the user asks about document properties
    such as title, author, upload date, field, or subject area — without
    needing to do a full-text search.
    """
    from db import Document as DocModel, db_session

    ctx = runtime.context
    with db_session() as db:
        docs = db.query(DocModel).filter(DocModel.id.in_(ctx.document_ids or [])).all()
        result = []
        for doc in docs:
            result.append({
                "id": doc.id,
                "filename": doc.filename,
                "uploaded_at": doc.created_at.isoformat() if doc.created_at else None,
                "quality_issue": doc.quality_issue,
                "abstract_preview": (doc.abstract_text or "")[:400],
            })
        return json.dumps(result, ensure_ascii=False)


@tool
@observe(as_type="tool")
async def verify_claim(claim: str, runtime: ToolRuntime[AgentContext]) -> str:
    """
    Verify whether a specific factual claim is supported by the selected documents.
    Returns SUPPORTED, PARTIAL, or UNSUPPORTED with supporting evidence passages.

    Use when the user asks 'did this paper really say X?', when you need to
    fact-check a statement before including it in your answer, or when you
    suspect a claim may be hallucinated.

    claim: a specific factual statement to verify against the documents
           (e.g. '本研究使用分類歸納法', '研究對象為 200 名高中生')
    """
    import re as _re

    raw = await run_search_report(
        query=claim,
        ctx=runtime.context,
        keyword_query=claim,
        semantic_query=f"文件中是否提到：{claim}",
    )
    chunks = json.loads(raw).get("results", [])

    if not chunks:
        return json.dumps({
            "verdict": "UNSUPPORTED", "confidence": 0.0, "evidence": [],
            "explanation": "找不到相關文件片段，無法驗證此聲明。",
        }, ensure_ascii=False)

    passages = [{"page": c.get("page"), "content": str(c.get("content", ""))[:600]} for c in chunks[:4]]
    try:
        resp = await _get_query_expander_llm().ainvoke([
            SystemMessage(content=_VERIFY_CLAIM_SYSTEM),
            HumanMessage(content=json.dumps({"claim": claim, "passages": passages}, ensure_ascii=False)),
        ])
        text = resp.content.strip()
        text = _re.sub(r"^```(?:json)?\s*", "", text)
        text = _re.sub(r"\s*```$", "", text)
        return text
    except Exception as exc:
        return json.dumps({
            "verdict": "PARTIAL", "confidence": 0.5,
            "evidence": [str(c.get("content", ""))[:300] for c in chunks[:2]],
            "explanation": f"驗證失敗：{exc}",
        }, ensure_ascii=False)


@tool
@observe(as_type="tool")
async def search_by_section(section: str, runtime: ToolRuntime[AgentContext]) -> str:
    """
    Retrieve chunks from a specific document section by name.

    Use this when the user asks about a specific chapter or section — e.g.
    '研究方法章節', 'conclusion', '結論', '緒論' — and you want section-level
    precision without relying purely on keyword search.

    section: section name in Chinese or English (e.g. '研究方法', 'methods',
             '結論', 'conclusion', '文獻回顧', 'related_work')
    """
    from qdrant_client.models import Filter, FieldCondition, MatchAny, MatchValue
    from rag import get_vectorstore, get_dense_vectorstore, aget_document_language, RETRIEVAL_K, search_documents

    ctx = runtime.context
    canonical = _resolve_section(section)

    must: list = []
    if ctx.document_ids:
        must.append(FieldCondition(key="metadata.document_id", match=MatchAny(any=[str(did) for did in ctx.document_ids])))
    must.append(FieldCondition(key="metadata.section", match=MatchValue(value=canonical)))
    qdrant_filter = Filter(must=must)

    lang = await aget_document_language(ctx.document_ids)
    vs = get_dense_vectorstore() if lang == "en" else get_vectorstore()
    hits = await vs.asimilarity_search(section, k=RETRIEVAL_K, filter=qdrant_filter)

    if not hits:
        aliases = _SECTION_ALIASES.get(canonical, [section])
        chunks, _sources = await search_documents(queries=[section, canonical, " ".join(aliases)], document_ids=ctx.document_ids, top_n=6, lang=lang)
        if not chunks:
            return json.dumps({"results": [], "message": f"No chunks found for section '{section}' (resolved: '{canonical}')."}, ensure_ascii=False)
        return json.dumps({"results": chunks[:6], "fallback": "heading_keyword"}, ensure_ascii=False)

    chunks = [{"filename": doc.metadata.get("filename", ""), "page": doc.metadata.get("page"), "section": doc.metadata.get("section", ""), "content": doc.page_content[:900]} for doc in hits[:6]]
    return json.dumps({"results": chunks, "fallback": "none"}, ensure_ascii=False)


@tool
@observe(as_type="tool")
async def compare_documents(query: str, runtime: ToolRuntime[AgentContext]) -> str:
    """
    Search each selected document separately for the same query and return results
    grouped by document, so you can compare what different documents say about a topic.

    Use when the user wants to compare information across multiple documents — e.g.
    'compare the research methods in both papers' or '這兩篇對水意象的分析有何不同'.
    Requires at least 2 documents to be selected.

    query: what to look for in each document
    """
    from qdrant_client.models import Filter, FieldCondition, MatchAny
    from rag import get_vectorstore, get_dense_vectorstore, aget_document_language
    from db import Document as DocModel, db_session

    ctx = runtime.context
    if not ctx.document_ids or len(ctx.document_ids) < 2:
        return json.dumps({"message": "compare_documents requires at least 2 documents selected."}, ensure_ascii=False)

    with db_session() as db:
        rows = db.query(DocModel.id, DocModel.filename).filter(DocModel.id.in_(ctx.document_ids)).all()
        id_to_name = {row.id: row.filename for row in rows}

    lang = await aget_document_language(ctx.document_ids)
    vs = get_dense_vectorstore() if lang == "en" else get_vectorstore()

    comparison: dict[str, list[dict]] = {}
    for doc_id in ctx.document_ids:
        doc_filter = Filter(must=[FieldCondition(key="metadata.document_id", match=MatchAny(any=[str(doc_id)]))])
        hits = await vs.asimilarity_search(query, k=3, filter=doc_filter)
        comparison[id_to_name.get(doc_id, str(doc_id))] = [
            {"page": doc.metadata.get("page"), "section": doc.metadata.get("section", ""), "content": doc.page_content[:700]}
            for doc in hits
        ]

    return json.dumps({"query": query, "comparison": comparison}, ensure_ascii=False)


@tool
@observe(as_type="tool")
def web_search(query: str) -> str:
    """
    Search public web for external or recent information.

    Prefer search_report first for file-related questions.

    Note: uses synchronous DDGS intentionally — LangChain runs @tool in a
    thread pool so this does not block the FastAPI event loop. If this is ever
    converted to async def, switch to AsyncDDGS or wrap with asyncio.to_thread.
    """
    from duckduckgo_search import DDGS

    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=5))
        return json.dumps(
            [{"title": r["title"], "url": r["href"], "content": r["body"]} for r in results],
            ensure_ascii=False,
        )
    except Exception as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)


TOOLS = [
    search_report,
    search_by_section,
    compare_documents,
    verify_claim,
    list_documents,
    get_document_metadata,
    web_search,
]

__all__ = [
    "AgentContext",
    "SearchInput",
    "TOOLS",
    "compare_documents",
    "detect_document_language",
    "expand_queries",
    "get_document_metadata",
    "list_documents",
    "run_search_report",
    "search_by_section",
    "search_report",
    "set_query_expander_llm",
    "verify_claim",
    "web_search",
]
