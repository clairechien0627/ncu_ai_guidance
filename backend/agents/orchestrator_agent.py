from collections.abc import AsyncIterator
from typing import Callable

from .runner import run_orchestrator_agent, run_orchestrator_agent_stream
from prompting.loader import load_stack

from .types import AgentResult

STACK_NAME = "chat_default"
PROMPT_NAME = "chat_mode"
AGENT_NAME = "orchestrator_agent"


def trace_metadata() -> dict[str, str | int]:
    stack = load_stack(STACK_NAME)
    return {
        "mode": "chat",
        "agent_name": AGENT_NAME,
        **stack.metadata(),
    }


async def answer(
    user_message: str,
    thread_id: str,
    document_ids: list[int] | None = None,
    *,
    run_id: str | None = None,
    on_stage: Callable[[str], None] | None = None,
    use_mini: bool = False,
    original_intent: str | None = None,
    resolved_intent: str | None = None,
) -> AgentResult:
    stack = load_stack(STACK_NAME)
    meta = {"mode": "chat", "agent_name": AGENT_NAME, **stack.metadata()}
    if original_intent:
        meta["original_intent"] = original_intent
    if resolved_intent:
        meta["resolved_intent"] = resolved_intent
    response, sources = await run_orchestrator_agent(
        user_message,
        thread_id,
        document_ids,
        metadata=meta,
        task_prompt=stack.contents,
        run_id=run_id,
        on_stage=on_stage,
        use_mini=use_mini,
    )
    return AgentResult(
        response=response,
        sources=sources,
        mode="chat",
        agent_name=AGENT_NAME,
        prompt_name=str(meta.get("prompt_name", PROMPT_NAME)),
        prompt_version=str(meta.get("prompt_version", "unknown")),
        trace_run_id=run_id,
        next_intent=None,
    )


async def stream(
    user_message: str,
    thread_id: str,
    document_ids: list[int] | None = None,
    *,
    run_id: str | None = None,
    on_stage: Callable[[str], None] | None = None,
    use_mini: bool = False,
    original_intent: str | None = None,
    resolved_intent: str | None = None,
) -> AsyncIterator[tuple[str, bool, list[str]]]:
    stack = load_stack(STACK_NAME)
    meta = {"mode": "chat", "agent_name": AGENT_NAME, **stack.metadata()}
    if original_intent:
        meta["original_intent"] = original_intent
    if resolved_intent:
        meta["resolved_intent"] = resolved_intent
    async for item in run_orchestrator_agent_stream(
        user_message,
        thread_id,
        document_ids,
        metadata=meta,
        task_prompt=stack.contents,
        run_id=run_id,
        on_stage=on_stage,
        use_mini=use_mini,
    ):
        yield item
