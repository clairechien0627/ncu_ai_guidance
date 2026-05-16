import hashlib
import json
import logging
from dataclasses import dataclass, field
from typing import Callable

logger = logging.getLogger(__name__)

from langchain.tools import ToolRuntime
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from rag import (
    count_document_chunks as _count_document_chunks,
    get_document_language as _get_document_language,
    search_documents as _search_documents,
)


@dataclass
class AgentContext:
    """Runtime state shared across tool calls during one request."""

    document_ids: list[int] | None = None
    seen_chunks: set = field(default_factory=set)
    search_count: int = 0
    consecutive_empty: int = 0
    on_stage: Callable[[str], None] | None = None
    task_type: str | None = None
    route_intent: str | None = None
    max_searches: int | None = None
    max_consecutive_empty: int | None = None
    thread_id: str | None = None
    run_id: str | None = None
    tool_sources: list[str] = field(default_factory=list)
    # Per-request cache for _memory_prompt: built once, reused for every model call
    # within the same agent run so pgvector/DB are not queried on every model call.
    _memory_context: dict | None = field(default=None, repr=False)


class SearchInput(BaseModel):
    display_intent: str = Field(
        default="",
        description="Optional short UI label for this search. Not used for retrieval.",
    )
    keyword_query: str = Field(
        default="",
        description="Optional keyword-style query for sparse/BM25 retrieval.",
    )
    semantic_query: str = Field(
        default="",
        description="Optional semantic sentence for dense retrieval.",
    )
    section_terms: list[str] = Field(
        default_factory=list,
        description="Optional section or heading terms such as conclusion, method, limitation.",
    )
    query: str = Field(
        default="",
        description=(
            "Precise search query for ONE focused topic only. "
            "For follow-up searches, include 1-3 document-specific terms learned from prior evidence. "
            "Avoid repeating generic slot names only."
        ),
    )
    sub_queries: list[str] = Field(
        default_factory=list,
        description=(
            "Optional alternative phrasings of the SAME topic, max 2. "
            "Do not mix unrelated slots such as method and results in one sub-query list."
        ),
    )
    use_hyde: bool = Field(
        default=False,
        description="Enable one HyDE expansion only when normal retrieval is weak or empty.",
    )


_HYDE_PROMPT = (
    "Write a short hypothetical passage (2-4 sentences) that could appear in the same "
    "research report and would help retrieve evidence for the query. Use only the topic, "
    "known evidence terms, gaps, and search intent supplied by the query. Do not invent "
    "facts as final evidence. Return only the passage."
)

_query_expander_llm = None
_MAX_SEARCHES = 8
_MAX_CONSECUTIVE_EMPTY = 3


def set_query_expander_llm(llm) -> None:
    """Set the LLM used for HyDE query expansion. Called at startup by runner.py."""
    global _query_expander_llm
    _query_expander_llm = llm


def _get_query_expander_llm():
    """Return the expander LLM, lazily initialising from settings if not yet injected."""
    global _query_expander_llm
    if _query_expander_llm is None:
        from langchain_openai import AzureChatOpenAI
        from config import settings
        _query_expander_llm = AzureChatOpenAI(
            azure_deployment=settings.azure_chat_deployment,
            azure_endpoint=settings.azure_openai_endpoint,
            api_key=settings.azure_openai_api_key.get_secret_value(),
            api_version=settings.azure_openai_api_version,
            temperature=0.3,
        )
    return _query_expander_llm


def expand_queries(
    query: str,
    sub_queries: list[str] | None = None,
    target_lang: str = "auto",
    keyword_query: str = "",
    semantic_query: str = "",
    section_terms: list[str] | None = None,
    use_hyde: bool = False,
) -> list[str]:
    """Expand retrieval query with role-specific query forms and optional HyDE."""
    raw_queries = [
        keyword_query,
        semantic_query,
        " ".join(section_terms or []),
        query,
        *(sub_queries or [])[:2],
    ]
    queries: list[str] = []
    for raw in raw_queries:
        cleaned = " ".join(str(raw or "").split())
        if cleaned and cleaned not in queries:
            queries.append(cleaned)

    if not queries:
        return []

    if not use_hyde:
        return queries

    try:
        hyde = _get_query_expander_llm().invoke(
            [
                SystemMessage(content=_HYDE_PROMPT),
                HumanMessage(
                    content=(
                        f"semantic_query: {semantic_query or query or keyword_query}\n"
                        f"keyword_query: {keyword_query}\n"
                        f"section_terms: {' '.join(section_terms or [])}"
                    )
                ),
            ]
        )
        passage = hyde.content.strip()
        if passage and passage not in queries:
            queries.append(passage)
    except Exception:
        pass

    return queries


from langfuse import observe

def run_search_report(
    query: str,
    ctx: AgentContext,
    sub_queries: list[str] | None = None,
    display_intent: str = "",
    keyword_query: str = "",
    semantic_query: str = "",
    section_terms: list[str] | None = None,
    use_hyde: bool = False,
) -> str:
    """Shared implementation for the LangChain tool and research_graph retriever."""
    max_searches = ctx.max_searches or _MAX_SEARCHES
    max_empty = ctx.max_consecutive_empty or _MAX_CONSECUTIVE_EMPTY

    if ctx.search_count >= max_searches:
        return json.dumps(
            {
                "results": [],
                "HARD_STOP": (
                    f"Search limit reached ({max_searches}). "
                    "Use collected evidence to answer."
                ),
            },
            ensure_ascii=False,
        )

    if ctx.consecutive_empty >= max_empty:
        return json.dumps(
            {
                "results": [],
                "HARD_STOP": (
                    f"{max_empty} consecutive searches found nothing new. "
                    "Use collected evidence to answer."
                ),
            },
            ensure_ascii=False,
        )

    ctx.search_count += 1

    if ctx.on_stage:
        try:
            label = display_intent or keyword_query or query
            short_q = label[:30] + ("..." if len(label) > 30 else "")
            ctx.on_stage(f"搜尋文件：{short_q}")
        except Exception:
            pass

    total_chunks = _count_document_chunks(ctx.document_ids or None)
    if total_chunks > 0 and len(ctx.seen_chunks) >= total_chunks:
        return json.dumps(
            {
                "results": [],
                "HARD_STOP": (
                    f"All {total_chunks} chunks already reviewed. "
                    "Use collected evidence to answer."
                ),
            },
            ensure_ascii=False,
        )

    lang = _get_document_language(ctx.document_ids or None)
    queries = expand_queries(
        query,
        sub_queries,
        target_lang=lang,
        keyword_query=keyword_query,
        semantic_query=semantic_query,
        section_terms=section_terms,
        use_hyde=use_hyde,
    )
    if not queries:
        ctx.consecutive_empty += 1
        return json.dumps(
            {
                "results": [],
                "message": "No retrieval query was provided.",
            },
            ensure_ascii=False,
        )
    num_docs = len(ctx.document_ids) if ctx.document_ids else 1
    top_n = min(num_docs * 3, 12) if num_docs > 1 else 4

    chunks, _ = _search_documents(
        queries,
        document_ids=ctx.document_ids or None,
        top_n=top_n,
        lang=lang,
        exclude_chunk_keys=ctx.seen_chunks,
    )

    # HyDE fallback: if the planner didn't request HyDE and we got zero results,
    # retry once with a hypothetical document. This reduces false negatives caused
    # by vocabulary mismatch without burning an extra search_count slot.
    if not chunks and not use_hyde:
        hyde_queries = expand_queries(
            query, sub_queries,
            target_lang=lang,
            keyword_query=keyword_query,
            semantic_query=semantic_query,
            section_terms=section_terms,
            use_hyde=True,
        )
        if len(hyde_queries) > len(queries):  # HyDE actually added something new
            chunks, _ = _search_documents(
                hyde_queries,
                document_ids=ctx.document_ids or None,
                top_n=top_n,
                lang=lang,
                exclude_chunk_keys=ctx.seen_chunks,
            )
            if chunks:
                logger.debug(
                    "run_search_report: HyDE fallback found %d chunks for %r",
                    len(chunks), query[:60],
                )

    for chunk in chunks:
        ctx.seen_chunks.add(hashlib.md5(chunk["content"].encode("utf-8", errors="replace")).hexdigest())

    if not chunks:
        ctx.consecutive_empty += 1
        return json.dumps(
            {
                "results": [],
                "message": (
                    "No results found. "
                    "Try narrower keywords, synonyms, or related concepts."
                ),
            },
            ensure_ascii=False,
        )

    ctx.consecutive_empty = 0
    return json.dumps({"results": chunks}, ensure_ascii=False)


@tool(args_schema=SearchInput)
@observe(as_type="tool")
def search_report(
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
    return run_search_report(
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
def detect_document_language(runtime: ToolRuntime[AgentContext]) -> str:
    """Detect primary language of uploaded documents."""
    ctx = runtime.context
    if ctx.on_stage:
        try:
            ctx.on_stage("偵測文件語言")
        except Exception:
            pass
    lang = _get_document_language(ctx.document_ids or None)
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
            tags: list[str] = []
            if doc.tags:
                try:
                    import json as _json
                    tags = _json.loads(doc.tags)
                except Exception:
                    tags = [doc.tags]
            result.append({
                "id": doc.id,
                "filename": doc.filename,
                "uploaded_at": doc.created_at.isoformat() if doc.created_at else None,
                "category": doc.category,
                "tags": tags,
                "quality_issue": doc.quality_issue,
                "abstract_preview": (doc.abstract_text or "")[:400],
            })
        return json.dumps(result, ensure_ascii=False)


_VERIFY_CLAIM_SYSTEM = (
    "你是文件事實核查員。給定一個聲明和相關文件片段，判斷文件原文是否支撐該聲明。\n"
    "只依據文件原文判斷，不補充外部知識。\n\n"
    "輸出純 JSON（不加 markdown）：\n"
    '{"verdict": "SUPPORTED|PARTIAL|UNSUPPORTED", '
    '"confidence": 0.0-1.0, '
    '"evidence": ["直接引文或片段（繁體中文）"], '
    '"explanation": "一兩句說明（繁體中文）"}'
)


@tool
@observe(as_type="tool")
def verify_claim(claim: str, runtime: ToolRuntime[AgentContext]) -> str:
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

    # Step 1: retrieve relevant passages via normal RAG search
    raw = run_search_report(
        query=claim,
        ctx=runtime.context,
        keyword_query=claim,
        semantic_query=f"文件中是否提到：{claim}",
    )
    chunks = json.loads(raw).get("results", [])

    if not chunks:
        return json.dumps({
            "verdict": "UNSUPPORTED",
            "confidence": 0.0,
            "evidence": [],
            "explanation": "找不到相關文件片段，無法驗證此聲明。",
        }, ensure_ascii=False)

    # Step 2: LLM verification
    passages = [
        {"page": c.get("page"), "content": str(c.get("content", ""))[:600]}
        for c in chunks[:4]
    ]
    try:
        resp = _get_query_expander_llm().invoke([
            SystemMessage(content=_VERIFY_CLAIM_SYSTEM),
            HumanMessage(content=json.dumps(
                {"claim": claim, "passages": passages},
                ensure_ascii=False,
            )),
        ])
        text = resp.content.strip()
        text = _re.sub(r"^```(?:json)?\s*", "", text)
        text = _re.sub(r"\s*```$", "", text)
        return text
    except Exception as exc:
        return json.dumps({
            "verdict": "PARTIAL",
            "confidence": 0.5,
            "evidence": [str(c.get("content", ""))[:300] for c in chunks[:2]],
            "explanation": f"驗證失敗：{exc}",
        }, ensure_ascii=False)


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


@tool
@observe(as_type="tool")
def search_by_section(section: str, runtime: ToolRuntime[AgentContext]) -> str:
    """
    Retrieve chunks from a specific document section by name.

    Use this when the user asks about a specific chapter or section — e.g.
    '研究方法章節', 'conclusion', '結論', '緒論' — and you want section-level
    precision without relying purely on keyword search.

    section: section name in Chinese or English (e.g. '研究方法', 'methods',
             '結論', 'conclusion', '文獻回顧', 'related_work')
    """
    from qdrant_client.models import Filter, FieldCondition, MatchAny, MatchValue
    from rag import (
        get_vectorstore,
        get_dense_vectorstore,
        get_document_language,
        RETRIEVAL_K,
        search_documents,
    )

    ctx = runtime.context
    canonical = _resolve_section(section)

    must: list = []
    if ctx.document_ids:
        must.append(FieldCondition(
            key="metadata.document_id",
            match=MatchAny(any=[str(did) for did in ctx.document_ids]),
        ))
    must.append(FieldCondition(key="metadata.section", match=MatchValue(value=canonical)))
    qdrant_filter = Filter(must=must)

    lang = get_document_language(ctx.document_ids)
    vs = get_dense_vectorstore() if lang == "en" else get_vectorstore()
    hits = vs.similarity_search(section, k=RETRIEVAL_K, filter=qdrant_filter)

    fallback_used = False
    if not hits:
        aliases = _SECTION_ALIASES.get(canonical, [section])
        fallback_queries = [section, canonical, " ".join(aliases)]
        chunks, _sources = search_documents(
            queries=fallback_queries,
            document_ids=ctx.document_ids,
            top_n=6,
            lang=lang,
        )
        fallback_used = True
        if not chunks:
            return json.dumps(
                {"results": [], "message": f"No chunks found for section '{section}' (resolved: '{canonical}')."},
                ensure_ascii=False,
            )
        return json.dumps({"results": chunks[:6], "fallback": "heading_keyword"}, ensure_ascii=False)

    chunks = [
        {
            "filename": doc.metadata.get("filename", ""),
            "page": doc.metadata.get("page"),
            "section": doc.metadata.get("section", ""),
            "content": doc.page_content[:900],
        }
        for doc in hits[:6]
    ]
    return json.dumps({"results": chunks, "fallback": "none" if not fallback_used else "heading_keyword"}, ensure_ascii=False)


@tool
@observe(as_type="tool")
def compare_documents(query: str, runtime: ToolRuntime[AgentContext]) -> str:
    """
    Search each selected document separately for the same query and return results
    grouped by document, so you can compare what different documents say about a topic.

    Use when the user wants to compare information across multiple documents — e.g.
    'compare the research methods in both papers' or '這兩篇對水意象的分析有何不同'.
    Requires at least 2 documents to be selected.

    query: what to look for in each document
    """
    from qdrant_client.models import Filter, FieldCondition, MatchAny
    from rag import get_vectorstore, get_dense_vectorstore, get_document_language
    from db import Document as DocModel, db_session

    ctx = runtime.context
    if not ctx.document_ids or len(ctx.document_ids) < 2:
        return json.dumps(
            {"message": "compare_documents requires at least 2 documents selected."},
            ensure_ascii=False,
        )

    with db_session() as db:
        rows = db.query(DocModel.id, DocModel.filename).filter(DocModel.id.in_(ctx.document_ids)).all()
        id_to_name = {row.id: row.filename for row in rows}

    lang = get_document_language(ctx.document_ids)
    vs = get_dense_vectorstore() if lang == "en" else get_vectorstore()

    comparison: dict[str, list[dict]] = {}
    for doc_id in ctx.document_ids:
        doc_filter = Filter(must=[FieldCondition(
            key="metadata.document_id",
            match=MatchAny(any=[str(doc_id)]),
        )])
        hits = vs.similarity_search(query, k=3, filter=doc_filter)
        comparison[id_to_name.get(doc_id, str(doc_id))] = [
            {
                "page": doc.metadata.get("page"),
                "section": doc.metadata.get("section", ""),
                "content": doc.page_content[:700],
            }
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
            [
                {
                    "title": result["title"],
                    "url": result["href"],
                    "content": result["body"],
                }
                for result in results
            ],
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
