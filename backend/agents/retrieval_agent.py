from utils import new_id
from collections.abc import AsyncIterator, Callable

from .runner import run_tool_agent as _run_agent
from .runner import run_tool_agent_stream as _run_agent_stream
from .no_tool_runner import write_agent_span
from prompting.loader import load_stack

from .types import AgentResult

STACK_NAME = "retrieval_default"
PROMPT_NAME = "retrieval_capability"
AGENT_NAME = "retrieval_agent"

# Keywords that suggest the user wanted comprehensive multi-aspect research,
# not a quick single-point answer. If retrieval finds nothing, escalate.
_RESEARCH_ESCALATION_KEYWORDS = (
    "摘要", "總結", "重點整理", "懶人包",
    "研究動機", "研究方法", "研究成果", "研究限制",
    "動機方法成果", "完整分析", "全面整理",
    "summary", "summarize", "overview",
)


def trace_metadata(route_intent: str = "retrieval") -> dict[str, str | int]:
    stack = load_stack(STACK_NAME)
    return {
        "task_type": "retrieval_qa",
        "route_intent": route_intent,
        "agent_name": AGENT_NAME,
        **stack.metadata(),
    }


async def answer(
    user_message: str,
    thread_id: str,
    document_ids: list[int] | None = None,
    *,
    route_intent: str = "retrieval",
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
    original_intent: str | None = None,
    resolved_intent: str | None = None,
) -> AgentResult:
    from datetime import datetime, timezone as _tz
    import uuid as _uuid
    stack = load_stack(STACK_NAME)
    metadata = metadata or {
        "task_type": "retrieval_qa",
        "route_intent": route_intent,
        "agent_name": AGENT_NAME,
        **stack.metadata(),
    }
    if original_intent and "original_intent" not in metadata:
        metadata["original_intent"] = original_intent
    if resolved_intent and "resolved_intent" not in metadata:
        metadata["resolved_intent"] = resolved_intent

    observation_id = observation_id or new_id()
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
    next_intent = None
    if (
        document_ids
        and not sources
        and any(kw in user_message.lower() for kw in _RESEARCH_ESCALATION_KEYWORDS)
    ):
        next_intent = "research"

    return AgentResult(
        response=response,
        sources=sources,
        task_type=str(metadata.get("task_type") or "retrieval_qa"),
        route_intent=str(metadata.get("route_intent") or route_intent),
        agent_name=metadata.get("agent_name", AGENT_NAME),
        prompt_name=metadata.get("prompt_name", PROMPT_NAME),
        prompt_version=metadata.get("prompt_version", "unknown"),
        observation_id=observation_id,
        next_intent=next_intent,
    )


async def stream(
    user_message: str,
    thread_id: str,
    document_ids: list[int] | None = None,
    *,
    route_intent: str = "retrieval",
    task_prompt: str | list[str] | None = None,
    metadata: dict[str, str | int] | None = None,
    observation_id: str | None = None,
    trace_id: str | None = None,
    include_document_abstracts: bool = True,
    max_searches: int | None = None,
    max_consecutive_empty: int | None = None,
    use_mini: bool = False,
    original_intent: str | None = None,
    resolved_intent: str | None = None,
) -> AsyncIterator[tuple[str, bool, list[str]]]:
    stack = load_stack(STACK_NAME)
    metadata = metadata or {
        "task_type": "retrieval_qa",
        "route_intent": route_intent,
        "agent_name": AGENT_NAME,
        **stack.metadata(),
    }
    if original_intent and "original_intent" not in metadata:
        metadata["original_intent"] = original_intent
    if resolved_intent and "resolved_intent" not in metadata:
        metadata["resolved_intent"] = resolved_intent
    effective_prompt = task_prompt if task_prompt is not None else stack.contents
    async for item in _run_agent_stream(
        user_message,
        thread_id,
        document_ids,
        metadata=metadata,
        task_prompt=effective_prompt,
        observation_id=observation_id,
        trace_id=trace_id,
        include_document_abstracts=include_document_abstracts,
        include_research_context=False,
        max_searches=max_searches,
        max_consecutive_empty=max_consecutive_empty,
        use_mini=use_mini,
    ):
        yield item
