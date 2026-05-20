from ids import new_id
import re
import uuid
from collections.abc import AsyncIterator

from langfuse import observe
from prompting.loader import load_stack

from .no_tool_runner import run_no_tool_agent, stream_no_tool_agent, write_agent_span
from observability import update_current_observation_io
from .types import AgentResult

STACK_NAME = "question_default"
PROMPT_NAME = "question_skill"
AGENT_NAME = "question_agent"


def trace_metadata(thread_id: str | None = None, document_ids: list[int] | None = None) -> dict[str, str | int]:
    stack = load_stack(STACK_NAME, _prompt_key(thread_id, document_ids))
    return {
        "task_type": "question_generation",
        "route_intent": "question",
        "agent_name": AGENT_NAME,
        **stack.metadata(),
    }


def _prompt_key(thread_id: str | None, document_ids: list[int] | None) -> str:
    doc_key = ",".join(str(doc_id) for doc_id in sorted(document_ids or []))
    return f"{thread_id or ''}:{doc_key}"


def _has_questions(response: str) -> bool:
    markers = ("?", "\nQ", "\n-")
    return sum(response.count(marker) for marker in markers) >= 2


def _questions_have_varied_openings(response: str) -> bool:
    sentences = re.split(r"[?\n]", response)
    openings = [s.strip()[:4] for s in sentences if s.strip() and len(s.strip()) >= 4]
    if len(openings) < 2:
        return True
    return len(set(openings)) >= len(openings) - 1


@observe(as_type="agent", name="Question Agent", capture_input=False, capture_output=False)
async def answer(
    user_message: str,
    thread_id: str,
    document_ids: list[int] | None = None,
    *,
    observation_id: str | None = None,
    trace_id: str | None = None,
    use_mini: bool = False,
    original_intent: str | None = None,
    resolved_intent: str | None = None,
    evidence_context: str | None = None,
    evidence_sources: list[str] | None = None,
) -> AgentResult:
    extra_system_messages = [
        "You do not have retrieval tools in this step. Generate tutoring or "
        "student-facing questions only from the user request and any evidence "
        "context supplied by the router. Do not claim that you searched the "
        "documents yourself.",
    ]
    payload = {}
    if evidence_context:
        payload["evidence_context"] = evidence_context

    from datetime import datetime, timezone as _tz
    observation_id = observation_id or new_id()
    agent_start = datetime.now(_tz.utc)
    if trace_id:
        write_agent_span(
            observation_id=observation_id,
            trace_id=trace_id,
            thread_id=thread_id,
            parent_observation_id=trace_id,
            name="Question Agent",
            start_time=agent_start,
            input_data={"messages": [{"role": "user", "content": user_message}]},
            extra_metadata={"task_type": "question_generation", "agent_name": AGENT_NAME},
        )
    response, sources, meta, observation_id = await run_no_tool_agent(
        user_message=user_message,
        thread_id=thread_id,
        document_ids=document_ids,
        stack_name=STACK_NAME,
        prompt_name=PROMPT_NAME,
        agent_name=AGENT_NAME,
        task_type="question_generation",
        route_intent="question",
        observation_id=observation_id,
        trace_id=trace_id,
        use_mini=use_mini,
        extra_system_messages=extra_system_messages,
        payload=payload,
        sources=evidence_sources or [],
        original_intent=original_intent,
        resolved_intent=resolved_intent,
    )
    if trace_id:
        write_agent_span(
            observation_id=observation_id,
            trace_id=trace_id,
            thread_id=thread_id,
            parent_observation_id=trace_id,
            name="Question Agent",
            start_time=agent_start,
            end_time=datetime.now(_tz.utc),
            input_data={"messages": [{"role": "user", "content": user_message}]},
            output_data={"answer": response, "sources": sources},
            extra_metadata={"task_type": "question_generation", "agent_name": AGENT_NAME},
        )

    if not _has_questions(response) or not _questions_have_varied_openings(response):
        retry_observation_id = new_id() if observation_id else None
        r2, s2, _, _ = await run_no_tool_agent(
            user_message=user_message,
            thread_id=thread_id,
            document_ids=document_ids,
            stack_name=STACK_NAME,
            prompt_name=PROMPT_NAME,
            agent_name=AGENT_NAME,
            task_type="question_generation",
            route_intent="question",
            observation_id=retry_observation_id,
            trace_id=trace_id or observation_id,
            use_mini=use_mini,
            extra_system_messages=[
                *extra_system_messages,
                "Retry because the previous output did not contain enough varied questions.",
            ],
            payload=payload,
            sources=evidence_sources or [],
            original_intent=original_intent,
            resolved_intent=resolved_intent,
        )
        if _has_questions(r2):
            response, sources = r2, s2

    update_current_observation_io(
        input={
            "message": user_message,
            "document_ids": document_ids or [],
            "has_evidence_context": bool(evidence_context),
            "evidence_sources": evidence_sources or [],
        },
        output={"answer": response, "sources": sources},
        metadata={
            "agent_name": AGENT_NAME,
            "task_type": "question_generation",
            "prompt_stack_name": meta.get("prompt_stack_name"),
            "prompt_name": meta.get("prompt_name"),
            "prompt_version": meta.get("prompt_version"),
        },
    )

    return AgentResult(
        response=response,
        sources=sources,
        task_type="question_generation",
        route_intent="question",
        agent_name=AGENT_NAME,
        prompt_name=str(meta.get("prompt_name", PROMPT_NAME)),
        prompt_version=str(meta.get("prompt_version", "unknown")),
        observation_id=observation_id,
        next_intent=None,
    )


async def stream(
    user_message: str,
    thread_id: str,
    document_ids: list[int] | None = None,
    *,
    observation_id: str | None = None,
    trace_id: str | None = None,
    use_mini: bool = False,
    original_intent: str | None = None,
    resolved_intent: str | None = None,
    evidence_context: str | None = None,
    evidence_sources: list[str] | None = None,
) -> AsyncIterator[tuple[str, bool, list[str]]]:
    extra_system_messages = [
        "You do not have retrieval tools in this step. Generate tutoring or "
        "student-facing questions only from the user request and any evidence "
        "context supplied by the router. Do not claim that you searched the "
        "documents yourself.",
    ]
    payload = {}
    if evidence_context:
        payload["evidence_context"] = evidence_context

    async for token in stream_no_tool_agent(
        user_message=user_message,
        thread_id=thread_id,
        document_ids=document_ids,
        stack_name=STACK_NAME,
        prompt_name=PROMPT_NAME,
        agent_name=AGENT_NAME,
        task_type="question_generation",
        route_intent="question",
        observation_id=observation_id,
        trace_id=trace_id,
        use_mini=use_mini,
        extra_system_messages=extra_system_messages,
        payload=payload,
        sources=evidence_sources or [],
        original_intent=original_intent,
        resolved_intent=resolved_intent,
    ):
        yield token, False, []
    yield "", True, evidence_sources or []
