from __future__ import annotations

import json
import inspect
from collections.abc import Callable

from langfuse import observe
from observability import update_current_observation_io
from tools.rag_tool import AgentContext, run_search_report


def _source_for_chunk(chunk: dict) -> str:
    filename = chunk.get("filename", "Unknown")
    page = chunk.get("page", "?")
    page_end = chunk.get("page_end", page)
    return f"{filename} p.{page}-{page_end}" if page_end != page else f"{filename} p.{page}"


@observe(as_type="span", name="retriever", capture_input=False, capture_output=False)
async def retrieve_evidence(
    *,
    query: str,
    document_ids: list[int],
    seen_chunks: set[str],
    sub_queries: list[str] | None = None,
    display_intent: str = "",
    keyword_query: str = "",
    semantic_query: str = "",
    section_terms: list[str] | None = None,
    use_hyde: bool = False,
    on_stage: Callable[[str], None] | None = None,
    search_count: int = 0,
    consecutive_empty: int = 0,
    max_searches: int | None = None,
    max_consecutive_empty: int | None = None,
) -> tuple[list[dict], list[str]]:
    update_current_observation_io(input={
        "query": query,
        "keyword_query": keyword_query or query,
        "semantic_query": semantic_query,
        "section_terms": section_terms or [],
        "use_hyde": use_hyde,
        "document_ids": document_ids,
        "seen_chunks_count": len(seen_chunks),
    })
    ctx = AgentContext(
        document_ids=document_ids,
        seen_chunks=seen_chunks,
        search_count=search_count,
        consecutive_empty=consecutive_empty,
        on_stage=on_stage,
        max_searches=max_searches,
        max_consecutive_empty=max_consecutive_empty,
    )
    raw_result = run_search_report(
        query=query,
        ctx=ctx,
        sub_queries=sub_queries,
        display_intent=display_intent,
        keyword_query=keyword_query,
        semantic_query=semantic_query,
        section_terms=section_terms,
        use_hyde=use_hyde,
    )
    raw = await raw_result if inspect.isawaitable(raw_result) else raw_result
    payload = json.loads(raw)
    chunks = [dict(chunk) for chunk in payload.get("results", [])]
    sources = []
    for chunk in chunks:
        source = _source_for_chunk(chunk)
        if source not in sources:
            sources.append(source)
    update_current_observation_io(output={
        "chunk_count": len(chunks),
        "sources": sources,
    })
    return chunks, sources


def chunks_to_tool_json(chunks: list[dict]) -> str:
    return json.dumps({"results": chunks}, ensure_ascii=False)
