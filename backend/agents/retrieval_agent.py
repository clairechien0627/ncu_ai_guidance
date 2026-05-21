from utils import new_id
from collections.abc import AsyncIterator, Callable

from .runner import run_tool_agent as _run_agent
from .runner import run_tool_agent_stream as _run_agent_stream
from .no_tool_runner import write_agent_span
from prompting.loader import load_stack

from .types import AgentResult, AgentStatus


async def _emit_stage(on_stage, msg: str) -> None:
    if not on_stage:
        return
    result = on_stage(msg)
    if hasattr(result, "__await__"):
        await result

STACK_NAME = "retrieval_default"
PROMPT_NAME = "retrieval_capability"
AGENT_NAME = "retrieval_agent"

def trace_metadata() -> dict[str, str | int]:
    stack = load_stack(STACK_NAME)
    return {
        "task_type": "retrieval_qa",
        "agent_name": AGENT_NAME,
        **stack.metadata(),
    }


async def answer(
    user_message: str,
    thread_id: str,
    document_ids: list[int] | None = None,
    *,
    task_prompt: str | list[str] | None = None,
    on_stage: Callable[[str], None] | None = None,
    recursion_limit: int = 30,
    metadata: dict[str, str | int] | None = None,
    observation_id: str | None = None,
    include_document_abstracts: bool = True,
    max_searches: int | None = None,
    max_consecutive_empty: int | None = None,
    trace_id: str | None = None,
    use_mini: bool = False,
) -> AgentResult:
    from datetime import datetime, timezone as _tz
    import uuid as _uuid
    stack = load_stack(STACK_NAME)
    metadata = metadata or {
        "task_type": "retrieval_qa",
        "agent_name": AGENT_NAME,
        **stack.metadata(),
    }

    observation_id = observation_id or new_id()
    await _emit_stage(on_stage, "搜尋相關段落中")
    agent_start = datetime.now(_tz.utc)
    if trace_id:
        write_agent_span(
            observation_id=observation_id,
            trace_id=trace_id,
            thread_id=thread_id,
            parent_observation_id=trace_id,
            name="Retrieval Agent",
            start_time=agent_start,
            input_data={"messages": [{"role": "user", "content": user_message}]},
            extra_metadata={"task_type": "retrieval_qa", "agent_name": AGENT_NAME},
        )

    effective_prompt = task_prompt if task_prompt is not None else stack.contents
    response, sources = await _run_agent(
        user_message,
        thread_id,
        document_ids,
        metadata=metadata,
        observation_id=observation_id,
        task_prompt=effective_prompt,
        on_stage=on_stage,
        recursion_limit=recursion_limit,
        include_document_abstracts=include_document_abstracts,
        include_research_context=False,
        max_searches=max_searches,
        max_consecutive_empty=max_consecutive_empty,
        trace_id=trace_id,
        parent_observation_id=observation_id,
        use_mini=use_mini,
    )
    if trace_id:
        write_agent_span(
            observation_id=observation_id,
            trace_id=trace_id,
            thread_id=thread_id,
            parent_observation_id=trace_id,
            name="Retrieval Agent",
            start_time=agent_start,
            end_time=datetime.now(_tz.utc),
            input_data={"messages": [{"role": "user", "content": user_message}]},
            output_data={"answer": response, "sources": sources},
            extra_metadata={"task_type": "retrieval_qa", "agent_name": AGENT_NAME},
        )
    completed = bool(sources)
    return AgentResult(
        response=response,
        sources=sources,
        task_type=str(metadata.get("task_type") or "retrieval_qa"),
        agent_name=metadata.get("agent_name", AGENT_NAME),
        prompt_name=metadata.get("prompt_name", PROMPT_NAME),
        prompt_version=metadata.get("prompt_version", "unknown"),
        observation_id=observation_id,
        status=AgentStatus(
            completed=completed,
            work_summary=f"從文件中搜尋相關內容，找到 {len(sources)} 個來源。" if completed else "搜尋文件但未找到相關內容。",
            gaps=[] if completed else ["未找到與問題相關的文件片段"],
            agent_limitation="" if completed else "單點查找，無法跨文件深度綜合分析",
        ),
    )


async def stream(
    user_message: str,
    thread_id: str,
    document_ids: list[int] | None = None,
    *,
    task_prompt: str | list[str] | None = None,
    on_stage: Callable[[str], None] | None = None,
    metadata: dict[str, str | int] | None = None,
    observation_id: str | None = None,
    trace_id: str | None = None,
    include_document_abstracts: bool = True,
    max_searches: int | None = None,
    max_consecutive_empty: int | None = None,
    use_mini: bool = False,
) -> AsyncIterator[tuple[str, bool, list[str]]]:
    from datetime import datetime, timezone as _tz
    stack = load_stack(STACK_NAME)
    metadata = metadata or {
        "task_type": "retrieval_qa",
        "agent_name": AGENT_NAME,
        **stack.metadata(),
    }

    observation_id = observation_id or new_id()
    await _emit_stage(on_stage, "搜尋相關段落中")
    agent_start = datetime.now(_tz.utc)
    if trace_id:
        write_agent_span(
            observation_id=observation_id,
            trace_id=trace_id,
            thread_id=thread_id,
            parent_observation_id=trace_id,
            name="Retrieval Agent",
            start_time=agent_start,
            input_data={"messages": [{"role": "user", "content": user_message}]},
            extra_metadata={"task_type": "retrieval_qa", "agent_name": AGENT_NAME},
        )

    effective_prompt = task_prompt if task_prompt is not None else stack.contents
    last_sources: list[str] = []
    async for item in _run_agent_stream(
        user_message,
        thread_id,
        document_ids,
        metadata=metadata,
        task_prompt=effective_prompt,
        on_stage=on_stage,
        observation_id=observation_id,
        trace_id=trace_id,
        parent_observation_id=observation_id,  # LangChain runs hang under agent SPAN
        include_document_abstracts=include_document_abstracts,
        include_research_context=False,
        max_searches=max_searches,
        max_consecutive_empty=max_consecutive_empty,
        use_mini=use_mini,
    ):
        token, is_done, sources = item
        if is_done and sources:
            last_sources = sources
        yield item

    if trace_id:
        write_agent_span(
            observation_id=observation_id,
            trace_id=trace_id,
            thread_id=thread_id,
            parent_observation_id=trace_id,
            name="Retrieval Agent",
            start_time=agent_start,
            end_time=datetime.now(_tz.utc),
            output_data={"sources": last_sources},
            extra_metadata={"task_type": "retrieval_qa", "agent_name": AGENT_NAME},
        )
