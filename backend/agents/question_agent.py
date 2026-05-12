import logging
import re
from collections.abc import AsyncIterator

from .runner import run_specialist_agent as _run_agent
from prompting.loader import load_stack

from .types import AgentResult

logger = logging.getLogger(__name__)

STACK_NAME = "chat_question"
PROMPT_NAME = "question_skill"
AGENT_NAME = "question_agent"


def trace_metadata(thread_id: str | None = None, document_ids: list[int] | None = None) -> dict[str, str | int]:
    stack = load_stack(STACK_NAME, _prompt_key(thread_id, document_ids))
    return {
        "mode": "question",
        "agent_name": AGENT_NAME,
        **stack.metadata(),
    }


def _prompt_key(thread_id: str | None, document_ids: list[int] | None) -> str:
    doc_key = ",".join(str(doc_id) for doc_id in sorted(document_ids or []))
    return f"{thread_id or ''}:{doc_key}"


def _has_questions(response: str) -> bool:
    return "？" in response or "?" in response


def _questions_have_varied_openings(response: str) -> bool:
    sentences = re.split(r"[？?]", response)
    openings = [s.strip()[:4] for s in sentences if s.strip() and len(s.strip()) >= 4]
    if len(openings) < 2:
        return True
    return len(set(openings)) >= len(openings) - 1


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
) -> AgentResult:
    stack = load_stack(STACK_NAME, _prompt_key(thread_id, document_ids))
    meta = {"mode": "question", "agent_name": AGENT_NAME, **stack.metadata()}
    if original_intent:
        meta["original_intent"] = original_intent
    if resolved_intent:
        meta["resolved_intent"] = resolved_intent

    response, sources = await _run_agent(
        user_message,
        thread_id,
        document_ids,
        metadata=meta,
        task_prompt=stack.contents,
        run_id=run_id,
        parent_run_id=parent_run_id,
        use_mini=use_mini,
    )

    # Quality check: if no questions or all start the same way, retry once
    if not _has_questions(response) or not _questions_have_varied_openings(response):
        r2, s2 = await _run_agent(
            user_message, thread_id, document_ids,
            metadata=meta, task_prompt=stack.contents, run_id=run_id,
            parent_run_id=parent_run_id, use_mini=use_mini,
        )
        if _has_questions(r2):
            response, sources = r2, s2

    # Escalation: agent found nothing useful after two attempts → fall back to
    # research_agent (collects comprehensive evidence) and re-run question generation
    # with the enriched context now available via the research context injection in
    # _build_messages. This keeps question_agent inside the multi-agent system without
    # needing to call extraction pipeline functions.
    if not sources and document_ids and not _has_questions(response):
        try:
            import uuid as _uuid
            from agents.research_agent import run_research_task
            research = await run_research_task(
                question=user_message,
                thread_id=thread_id,
                document_ids=document_ids,
                run_id=str(_uuid.uuid4()),
                metadata={"mode": "research", "agent_name": AGENT_NAME},
                mode="research",
            )
            if research.response:
                # Re-run question agent — _build_messages will now inject
                # the research result as "前次研究摘要" context
                r3, s3 = await _run_agent(
                    user_message, thread_id, document_ids,
                    metadata=meta, task_prompt=stack.contents, run_id=run_id,
                )
                if _has_questions(r3):
                    response, sources = r3, s3
        except Exception as exc:
            logger.warning("question_agent research escalation failed: %s", exc)

    return AgentResult(
        response=response,
        sources=sources,
        mode="question",
        agent_name=AGENT_NAME,
        prompt_name=str(meta.get("prompt_name", PROMPT_NAME)),
        prompt_version=str(meta.get("prompt_version", "unknown")),
        trace_run_id=run_id,
        next_intent=None,  # question_agent resolves its own escalation; never propagate outward
    )


async def stream(
    user_message: str,
    thread_id: str,
    document_ids: list[int] | None = None,
    *,
    run_id: str | None = None,
    original_intent: str | None = None,
    resolved_intent: str | None = None,
) -> AsyncIterator[tuple[str, bool, list[str]]]:
    result = await answer(user_message, thread_id, document_ids, run_id=run_id,
                          original_intent=original_intent, resolved_intent=resolved_intent)
    yield result.response, False, []
    yield "", True, result.sources
