import re
import uuid
from collections.abc import AsyncIterator

from langfuse import observe
from prompting.loader import load_stack

from .no_tool_runner import run_no_tool_agent
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
    run_id: str | None = None,
    parent_run_id: str | None = None,
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

    response, sources, meta, actual_run_id = await run_no_tool_agent(
        user_message=user_message,
        thread_id=thread_id,
        document_ids=document_ids,
        stack_name=STACK_NAME,
        prompt_name=PROMPT_NAME,
        agent_name=AGENT_NAME,
        task_type="question_generation",
        route_intent="question",
        run_id=run_id,
        parent_run_id=parent_run_id,
        use_mini=use_mini,
        extra_system_messages=extra_system_messages,
        payload=payload,
        sources=evidence_sources or [],
        original_intent=original_intent,
        resolved_intent=resolved_intent,
    )

    if not _has_questions(response) or not _questions_have_varied_openings(response):
        retry_run_id = str(uuid.uuid4()) if run_id else None
        r2, s2, _, _ = await run_no_tool_agent(
            user_message=user_message,
            thread_id=thread_id,
            document_ids=document_ids,
            stack_name=STACK_NAME,
            prompt_name=PROMPT_NAME,
            agent_name=AGENT_NAME,
            task_type="question_generation",
            route_intent="question",
            run_id=retry_run_id,
            parent_run_id=parent_run_id or run_id,
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
        trace_run_id=actual_run_id,
        next_intent=None,
    )


async def stream(
    user_message: str,
    thread_id: str,
    document_ids: list[int] | None = None,
    *,
    run_id: str | None = None,
    parent_run_id: str | None = None,
    use_mini: bool = False,
    original_intent: str | None = None,
    resolved_intent: str | None = None,
    evidence_context: str | None = None,
    evidence_sources: list[str] | None = None,
) -> AsyncIterator[tuple[str, bool, list[str]]]:
    result = await answer(
        user_message,
        thread_id,
        document_ids,
        run_id=run_id,
        parent_run_id=parent_run_id,
        use_mini=use_mini,
        original_intent=original_intent,
        resolved_intent=resolved_intent,
        evidence_context=evidence_context,
        evidence_sources=evidence_sources,
    )
    yield result.response, False, []
    yield "", True, result.sources
