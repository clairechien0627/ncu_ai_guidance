from utils import new_id
from collections.abc import AsyncIterator
import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
import uuid as _uuid_mod

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import AzureChatOpenAI
from pydantic import BaseModel, Field

from config import settings
from prompting.loader import load_stack
from prompting.registry import get as get_prompt
from prompting.registry import version as prompt_version

from .types import AgentRoute, AgentResult
from .request_context import get_user_id
from langfuse import observe, propagate_attributes
import contextlib

logger = logging.getLogger(__name__)


def _fire_and_forget(coro) -> None:
    """Schedule a coroutine as a background task, logging any exception."""
    task = asyncio.create_task(coro)
    task.add_done_callback(
        lambda t: logger.warning("Background task failed: %s", t.exception())
        if not t.cancelled() and t.exception()
        else None
    )


def _write_router_trace(
    trace_id: str,
    thread_id: str,
    document_ids: list[int] | None,
    route: AgentRoute,
    *,
    user_message: str | None = None,
    output: str | None = None,
    end_time: datetime | None = None,
    error: str | None = None,
    user_id: str | None = None,
) -> None:
    """Write or update a thin router-level trace entry (trace_id=None)."""
    import os
    from db import db_session, Trace
    from services.trace_capture import _get_default_environment
    from services.trace_ingestion import TraceEventIngestor

    now = datetime.now(timezone.utc)
    env = _get_default_environment()

    # v2 trace-create/update (always) — upsert semantics via outbox
    try:
        v2_body: dict = {
            "trace_id": trace_id,
            "name": "router_agent",
            "thread_id": thread_id,
            "user_id": user_id,
            "environment": env,
            "start_time": now.isoformat(),
            "metadata": {
                "route": route.resolved_intent or _normalise_intent(route.intent),
                "document_ids": document_ids,
                "original_intent": route.original_intent,
                "resolved_intent": route.resolved_intent,
            },
        }
        if user_message:
            v2_body["input"] = {"messages": [{"role": "user", "content": user_message}]}
        if output:
            v2_body["output"] = {"answer": output}
        if end_time:
            v2_body["end_time"] = end_time.isoformat()
        if error:
            v2_body["level"] = "ERROR"

        events: list[dict] = [{"event_type": "trace-create", "body": v2_body}]
        # Root observation — router_agent span (observation_id == trace_id so children can reference it)
        router_obs: dict = {
            "observation_id": trace_id,
            "trace_id": trace_id,
            "type": "SPAN",
            "name": "Router",
            "level": "ERROR" if error else "DEFAULT",
            "status_message": error,
            "start_time": now.isoformat(),
        }
        if user_message:
            router_obs["input"] = {"messages": [{"role": "user", "content": user_message}]}
        if output:
            router_obs["output"] = {"answer": output}
        if end_time:
            router_obs["end_time"] = end_time.isoformat()
        events.append({"event_type": "observation-create", "body": router_obs})
        TraceEventIngestor.enqueue_sync(events)
    except Exception as exc:
        logger.warning("Router v2 trace enqueue failed: %s", exc)

    # legacy write — kept for rollback; disable by setting LEGACY_TRACE_WRITE=false
    if os.environ.get("LEGACY_TRACE_WRITE", "true").lower() not in ("0", "false", "no"):
        try:
            with db_session() as db:
                existing = db.query(Trace).filter(Trace.observation_id == trace_id).first()
                if existing is None:
                    db.add(Trace(
                        observation_id=trace_id,
                        trace_id=None,
                        run_type="chain",
                        name="router_agent",
                        start_time=now,
                        thread_id=thread_id,
                        document_ids=json.dumps(document_ids) if document_ids else None,
                        task_type="routing",
                        route_intent=route.resolved_intent or _normalise_intent(route.intent),
                        agent_name="router_agent",
                        original_intent=route.original_intent,
                        resolved_intent=route.resolved_intent,
                        environment=env,
                        user_id=user_id,
                    ))
                else:
                    if end_time:
                        existing.end_time = end_time
                    if error:
                        existing.error = error
                db.commit()
        except Exception as exc:
            logger.warning("Router legacy trace write failed: %s", exc)

ROUTER_PROMPT_NAME = "route_coordinator"
VALID_INTENTS = {"chat", "retrieval", "research", "summary", "question", "evaluation"}

# Maximum number of times a single request may be re-routed between agents.
# If an agent tries to hand off more than this many times, we fall back to chat.
MAX_HANDOFFS = 3

_FOLLOWUP_SIGNALS = (
    "繼續", "再說", "補充", "更多", "詳細", "展開", "那", "那麼", "這個",
    "剛才", "你說的", "上面", "除此之外", "另外", "而且", "還有",
    "more", "continue", "elaborate", "expand", "follow up",
)


def _looks_like_followup(message: str) -> bool:
    """Short messages that appear to continue a previous topic."""
    text = (message or "").strip()
    if len(text) > 60:
        return False
    lower = text.lower()
    return any(sig in lower for sig in _FOLLOWUP_SIGNALS)


_RESEARCH_KEYWORDS = (
    "研究動機",
    "研究方法",
    "研究成果",
    "研究限制",
    "方法與結果",
    "動機方法成果",
    "motivation",
    "method",
    "results",
    "limitations",
)

_SUMMARY_KEYWORDS = (
    "摘要",
    "總結",
    "重點整理",
    "懶人包",
    "summary",
    "summarize",
    "overview",
)

_QUESTION_KEYWORDS = (
    "出題",
    "問我",
    "測驗",
    "題目",
    "導讀",
    "帶我理解",
    "帶我學",
    "興趣量表",
    "練習",
    "question",
    "questions",
    "quiz",
    "survey",
)

_RETRIEVAL_KEYWORDS = (
    "這篇",
    "這份",
    "主題",
    "在講什麼",
    "哪一頁",
    "引用",
    "來源",
    "根據文件",
    "根據 pdf",
    "根據pdf",
    "這段",
    "pdf",
    "source",
    "cite",
)

# Evaluation keywords are intentionally narrow: must refer to a *previous system response*,
# not to evaluating document content. Keyword path only catches the clearest cases;
# ambiguous phrasing falls through to LLM classification.
_EVALUATION_KEYWORDS = (
    "評估一下剛才",
    "review一下剛才",
    "review 一下剛才",
    "剛才的回答對嗎",
    "剛才的答案對嗎",
    "上一次的回答",
    "上一個答案好嗎",
    "check quality",
    "你剛才說的對嗎",
)


class RouterDecision(BaseModel):
    intent: str = Field(description="One of: chat, retrieval, research, question, summary, evaluation")
    evaluate_after: bool = Field(default=False, description="True only when user simultaneously asks a question AND explicitly requests quality verification of the answer")
    reason: str = Field(default="", description="One sentence explaining the routing decision")


@dataclass(frozen=True)
class ExecutionStep:
    """One router-owned agent invocation."""

    intent: str
    agent_name: str
    observation_id: str
    trace_id: str
    kind: str = "primary"
    reason: str = ""


@dataclass(frozen=True)
class ExecutionPlan:
    """Router execution plan for a single user request.

    Today this is intentionally one primary task-agent step, optional final
    composition, and optional background evaluation.
    """

    trace_id: str
    route: AgentRoute
    steps: tuple[ExecutionStep, ...]
    evaluate_after: bool = False

    @property
    def primary_step(self) -> ExecutionStep:
        return self.steps[0]

    @property
    def composition_step(self) -> ExecutionStep | None:
        return next((step for step in self.steps if step.kind == "composition"), None)

    @property
    def evidence_step(self) -> ExecutionStep | None:
        return next((step for step in self.steps if step.kind == "evidence_collection"), None)

    @property
    def target_step(self) -> ExecutionStep:
        return next((step for step in self.steps if step.kind == "primary"), self.steps[0])


def _prompt_key(thread_id: str | None, document_ids: list[int] | None) -> str:
    doc_key = ",".join(str(doc_id) for doc_id in sorted(document_ids or []))
    return f"{thread_id or ''}:{doc_key}"


def _normalise_intent(intent: str) -> str:
    value = (intent or "chat").strip().lower()
    if value == "summary":
        return "research"
    if value not in VALID_INTENTS:
        return "chat"
    return value


def _allows_background_evaluation(intent: str, has_docs: bool) -> bool:
    """Deterministic gate for router-triggered background evaluation.

    Explicit evaluation requests route directly to ``evaluation_agent``.  The
    background ``evaluate_after`` path is only for document-grounded task-agent
    outputs where an automatic quality pass is worth the extra cost.
    Extraction quality is handled separately by ``summary_quality``.
    """
    return has_docs and intent in {"research", "retrieval", "question"}


def _normalise_decision(decision: RouterDecision, has_docs: bool) -> RouterDecision:
    """Sanitise LLM output: fix invalid intents and enforce doc-gating rules."""
    intent = _normalise_intent(decision.intent)
    if intent in {"research", "retrieval"} and not has_docs:
        intent = "chat"
    evaluate_after = bool(decision.evaluate_after and _allows_background_evaluation(intent, has_docs))
    return RouterDecision(intent=intent, evaluate_after=evaluate_after, reason=decision.reason)


def _primary_prompt(stack_name: str, base_name: str, thread_id: str | None, document_ids: list[int] | None):
    stack = load_stack(stack_name, _prompt_key(thread_id, document_ids))
    return next((p for p in stack.prompts if p.base_name == base_name), stack.prompts[-1])


def _route_for_intent(
    intent: str,
    document_ids: list[int] | None = None,
    thread_id: str | None = None,
    *,
    evaluate_after: bool = False,
) -> AgentRoute:
    original_intent = (intent or "chat").strip().lower()
    route_intent = _normalise_intent(intent)
    if route_intent == "research":
        prompt = _primary_prompt("research_runtime", "research_writer", thread_id, document_ids)
        return AgentRoute(
            "research",
            "research_agent",
            prompt.name,
            prompt.version,
            original_intent=original_intent,
            resolved_intent="research",
            evaluate_after=evaluate_after,
        )
    if route_intent == "question":
        return AgentRoute(
            "question",
            "question_agent",
            "question_skill",
            prompt_version("question_skill"),
            original_intent=original_intent,
            resolved_intent="question",
            evaluate_after=evaluate_after,
        )
    if route_intent == "retrieval":
        return AgentRoute(
            "retrieval",
            "retrieval_agent",
            "retrieval_capability",
            prompt_version("retrieval_capability"),
            original_intent=original_intent,
            resolved_intent="retrieval",
            evaluate_after=evaluate_after,
        )
    if route_intent == "evaluation":
        return AgentRoute(
            "evaluation",
            "evaluation_agent",
            "evaluation_agent",
            prompt_version("evaluation_agent"),
            original_intent=original_intent,
            resolved_intent="evaluation",
            evaluate_after=False,
        )
    return AgentRoute(
        "chat",
        "chat_agent",
        "chat_mode",
        prompt_version("chat_mode"),
        original_intent=original_intent,
        resolved_intent="chat",
        evaluate_after=False,
    )


def _keyword_classify(message: str, has_docs: bool) -> str:
    text = (message or "").lower()
    # Evaluation doesn't require docs (reviewing a previous system response)
    if any(k.lower() in text for k in _EVALUATION_KEYWORDS):
        return "evaluation"
    if not has_docs:
        return "chat"
    if any(k.lower() in text for k in _QUESTION_KEYWORDS):
        return "question"
    if any(k.lower() in text for k in _SUMMARY_KEYWORDS):
        return "research"  # summary always routes to research; skip the normalise() detour
    if any(k.lower() in text for k in _RESEARCH_KEYWORDS):
        return "research"
    if any(k.lower() in text for k in _RETRIEVAL_KEYWORDS):
        return "retrieval"
    return "uncertain"


def _build_execution_plan(
    route: AgentRoute,
    *,
    trace_id: str,
    observation_id: str | None = None,
    collect_evidence: bool = False,
) -> ExecutionPlan:
    """Build the router-owned execution plan for a request."""
    intent = _normalise_intent(route.intent)
    agent_name = route.agent_name or _route_for_intent(intent).agent_name
    steps: list[ExecutionStep] = []
    if collect_evidence and intent == "question":
        steps.append(ExecutionStep(
            intent="retrieval",
            agent_name="retrieval_agent",
            observation_id=new_id(),
            trace_id=trace_id,
            kind="evidence_collection",
            reason="question_requires_document_evidence",
        ))
    steps.append(ExecutionStep(
        intent=intent,
        agent_name=agent_name,
        observation_id=observation_id or new_id(),
        trace_id=trace_id,
        kind="primary",
        reason=f"route_intent={intent}",
    ))
    if route.compose_after and intent not in {"chat", "evaluation"}:
        steps.append(ExecutionStep(
            intent="chat",
            agent_name="chat_agent",
            observation_id=new_id(),
            trace_id=trace_id,
            kind="composition",
            reason="final_composition",
        ))
    return ExecutionPlan(
        trace_id=trace_id,
        route=route,
        steps=tuple(steps),
        evaluate_after=route.evaluate_after,
    )


_router_llm = None


def _get_router_llm():
    """Create the router LLM lazily so importing this module does not require Azure credentials."""
    global _router_llm
    if _router_llm is None:
        _router_llm = AzureChatOpenAI(
            azure_deployment=settings.azure_mini_deployment or settings.azure_chat_deployment,
            azure_endpoint=settings.azure_openai_endpoint,
            api_key=settings.azure_openai_api_key.get_secret_value(),
            api_version=settings.azure_openai_api_version,
            temperature=0,
        )
    return _router_llm


async def _llm_classify_intent(message: str, has_docs: bool) -> RouterDecision:
    """Use route_coordinator for ambiguous requests. Falls back conservatively."""
    from observability import ainvoke_traced_generation

    stack = load_stack("router_default")
    system = get_prompt(ROUTER_PROMPT_NAME)
    user = {
        "message": message,
        "has_document_ids": has_docs,
        "allowed_intents": ["chat", "retrieval", "research", "question", "evaluation"],
        "response_format": {
            "intent": "chat|retrieval|research|question|evaluation",
            "evaluate_after": "boolean",
            "reason": "short reason",
        },
    }
    try:
        prompt = ChatPromptTemplate.from_messages([
            SystemMessage(content=system),
            ("human", "{payload}"),
        ])
        structured = _get_router_llm().with_structured_output(RouterDecision)
        chain = prompt | structured
        decision: RouterDecision = await ainvoke_traced_generation(
            chain,
            {"payload": json.dumps(user, ensure_ascii=False)},
            prompt_name=ROUTER_PROMPT_NAME,
            metadata={
                "task_type": "routing",
                "route_intent": "classification",
                "agent_name": "router_agent",
                **stack.metadata(),
            },
        )
        return _normalise_decision(decision, has_docs)
    except Exception as exc:
        logger.warning("LLM router failed; using conservative fallback: %s", exc)
        fallback = "retrieval" if has_docs else "chat"
        return RouterDecision(intent=fallback, evaluate_after=False, reason="fallback")


async def classify_intent(
    message: str,
    document_ids: list[int] | None = None,
    thread_id: str | None = None,
    previous_intent: str | None = None,
) -> AgentRoute:
    with propagate_attributes(session_id=thread_id, user_id=get_user_id()) if thread_id else contextlib.nullcontext():
        """Route a request with a deterministic fast path and an LLM fallback.

        previous_intent: the intent of the last turn in this conversation.
        Follow-up messages in research/retrieval conversations stay in the same
        route intent rather than falling through to chat.
        """
        has_docs = bool(document_ids)
        intent = _keyword_classify(message, has_docs)
        evaluate_after = False
        if intent == "uncertain":
            # Inherit previous intent for clear follow-up messages so "還有呢" or
            # "能補充嗎" don't get misrouted to chat mid-research.
            if previous_intent in ("research", "retrieval") and _looks_like_followup(message):
                intent = previous_intent
            else:
                decision = await _llm_classify_intent(message, has_docs)
                intent = decision.intent
                evaluate_after = decision.evaluate_after
        return _route_for_intent(intent, document_ids, thread_id, evaluate_after=evaluate_after)


async def _update_memory(
    intent: str,
    thread_id: str,
    user_id: str | None,
    document_ids: list[int] | None,
    question: str,
    result,
) -> None:
    """Fire-and-forget: write memory per intent policy via agent_memory contract."""
    from services.agent_memory import record_agent_memory
    await record_agent_memory(
        intent=intent,
        thread_id=thread_id,
        user_id=user_id,
        question=question,
        result=result,
        document_ids=document_ids,
    )


@observe(as_type="agent")
async def _run_evaluation_agent(
    thread_id: str,
    *,
    observation_id: str | None = None,
    trace_id: str | None = None,
    exclude_observation_id: str | None = None,
) -> AgentResult:
    with propagate_attributes(session_id=thread_id, user_id=get_user_id()) if thread_id else contextlib.nullcontext():
        from agents.evaluation_agent import evaluate_latest_trace_for_thread, format_evaluation_for_user
        result = await evaluate_latest_trace_for_thread(
            thread_id,
            exclude_observation_id=exclude_observation_id,
        )
        return AgentResult(
            response=format_evaluation_for_user(result),
            sources=[],
            task_type="evaluation",
            route_intent="evaluation",
            agent_name="evaluation_agent",
            prompt_name="evaluation_agent",
            prompt_version=prompt_version("evaluation_agent"),
            observation_id=observation_id,
        )


@observe(as_type="agent")
async def run_research_agent(
    user_message: str,
    thread_id: str,
    document_ids: list[int] | None,
    *,
    observation_id: str | None,
    trace_id: str | None = None,
    on_stage=None,
    on_token=None,
    original_intent: str | None = None,
    resolved_intent: str | None = None,
    bypass_cache: bool = False,
) -> AgentResult:
    with propagate_attributes(session_id=thread_id, user_id=get_user_id()) if thread_id else contextlib.nullcontext():
        from .research import run_research_task

        stack = load_stack("research_runtime", _prompt_key(thread_id, document_ids))
        metadata = {
            "task_type": "research_task",
            "route_intent": "research",
            "agent_name": "research_agent",
            "original_intent": original_intent or "research",
            "resolved_intent": resolved_intent or "research",
            **stack.metadata(),
        }
        return await run_research_task(
            question=user_message,
            thread_id=thread_id,
            document_ids=document_ids or [],
            task_type="research_task",
            metadata=metadata,
            observation_id=observation_id,
            on_stage=on_stage,
            on_token=on_token,
            max_searches=10,
            max_consecutive_no_new=2,
            trace_id=trace_id,
            bypass_cache=bypass_cache,
        )


@observe(as_type="agent")
async def run_chat_agent(
    user_message: str,
    thread_id: str,
    document_ids: list[int] | None,
    *,
    observation_id: str | None,
    trace_id: str | None = None,
    on_stage=None,
    use_mini: bool = False,
    original_intent: str | None = None,
    resolved_intent: str | None = None,
) -> AgentResult:
    with propagate_attributes(session_id=thread_id, user_id=get_user_id()) if thread_id else contextlib.nullcontext():
        from .chat_agent import answer as _chat_answer
        return await _chat_answer(
            user_message,
            thread_id,
            document_ids,
            observation_id=observation_id,
            trace_id=trace_id,
            on_stage=on_stage,
            use_mini=use_mini,
            original_intent=original_intent,
            resolved_intent=resolved_intent,
        )


@observe(as_type="chain")
async def route_agent_message(
    user_message: str,
    thread_id: str,
    document_ids: list[int] | None = None,
    *,
    route: AgentRoute | None = None,
    trace_id: str | None = None,
    _hop_count: int = 0,
    use_mini: bool = False,
) -> AgentResult:
    with propagate_attributes(session_id=thread_id, user_id=get_user_id()) if thread_id else contextlib.nullcontext():
        """Route a user message to the appropriate task agent."""
        from . import chat_agent, question_agent, retrieval_agent
        if _hop_count >= MAX_HANDOFFS:
            logger.warning(
                "Handoff limit reached (%d/%d) thread=%s; falling back to chat",
                _hop_count,
                MAX_HANDOFFS,
                thread_id,
            )
            return await chat_agent.answer(user_message, thread_id, document_ids,
                                           use_mini=use_mini)

        route = route or await classify_intent(user_message, document_ids, thread_id)

        # Router is the root trace; task agents become its children.
        trace_id = trace_id or new_id()
        plan = _build_execution_plan(
            route,
            trace_id=trace_id,
            collect_evidence=bool(document_ids) and _normalise_intent(route.intent) == "question",
        )
        step = plan.target_step
        route_intent = _normalise_intent(route.intent)
        await asyncio.to_thread(_write_router_trace, trace_id, thread_id, document_ids, route,
                                user_message=user_message, user_id=get_user_id())

        try:
            if route_intent == "evaluation":
                result = await _run_evaluation_agent(
                    thread_id,
                    observation_id=step.observation_id,
                    trace_id=step.trace_id,
                )
            elif route_intent == "research":
                result = await run_research_agent(
                    user_message,
                    thread_id,
                    document_ids,
                    observation_id=step.observation_id,
                    trace_id=step.trace_id,
                    original_intent=route.original_intent,
                    resolved_intent=route.resolved_intent,
                )
            elif route_intent == "question":
                evidence_context = None
                evidence_sources: list[str] = []
                evidence_step = plan.evidence_step
                if evidence_step is not None:
                    evidence = await retrieval_agent.answer(
                        user_message,
                        thread_id,
                        document_ids,
                        route_intent="question",
                        observation_id=evidence_step.observation_id,
                        trace_id=evidence_step.trace_id,
                        use_mini=use_mini,
                        original_intent=route.original_intent,
                        resolved_intent=route.resolved_intent,
                    )
                    evidence_context = evidence.response
                    evidence_sources = evidence.sources
                result = await question_agent.answer(
                    user_message,
                    thread_id,
                    document_ids,
                    observation_id=step.observation_id,
                    trace_id=step.trace_id,
                    use_mini=use_mini,
                    original_intent=route.original_intent,
                    resolved_intent=route.resolved_intent,
                    evidence_context=evidence_context,
                    evidence_sources=evidence_sources,
                )
            elif route_intent == "retrieval":
                result = await retrieval_agent.answer(
                    user_message,
                    thread_id,
                    document_ids,
                    route_intent=route_intent,
                    observation_id=step.observation_id,
                    trace_id=step.trace_id,
                    use_mini=use_mini,
                    original_intent=route.original_intent,
                    resolved_intent=route.resolved_intent,
                )
            else:
                result = await chat_agent.answer(
                    user_message,
                    thread_id,
                    document_ids,
                    observation_id=step.observation_id,
                    trace_id=step.trace_id,
                    use_mini=use_mini,
                    original_intent=route.original_intent,
                    resolved_intent=route.resolved_intent,
                )
        except Exception as exc:
            await asyncio.to_thread(
                _write_router_trace, trace_id, thread_id, document_ids, route,
                end_time=datetime.now(timezone.utc), error=str(exc), user_id=get_user_id(),
            )
            raise

        if result.next_intent and _hop_count + 1 < MAX_HANDOFFS:
            handoff_route = _route_for_intent(result.next_intent, document_ids, thread_id)
            return await route_agent_message(
                user_message,
                thread_id,
                document_ids,
                route=handoff_route,
                trace_id=trace_id,
                _hop_count=_hop_count + 1,
                use_mini=use_mini,
            )

        await asyncio.to_thread(
            _write_router_trace, trace_id, thread_id, document_ids, route,
            output=result.response if result else None,
            end_time=datetime.now(timezone.utc), user_id=get_user_id(),
        )

        composition_step = plan.composition_step
        if composition_step is not None:
            result = await chat_agent.compose_final_response(
                user_message=user_message,
                task_result=result,
                thread_id=thread_id,
                document_ids=document_ids,
                observation_id=composition_step.observation_id,
                trace_id=composition_step.trace_id,
                use_mini=use_mini,
            )

        if plan.evaluate_after:
            _fire_and_forget(_run_evaluation_agent(
                thread_id,
                trace_id=plan.trace_id,
                exclude_observation_id=result.observation_id,
            ))

        # Update memory per intent policy
        if route_intent in ("research", "summary"):
            _fire_and_forget(_update_memory(
                route_intent, thread_id, get_user_id(), document_ids, user_message, result
            ))

        return result


@observe(as_type="chain")
async def route_agent_stream(
    user_message: str,
    thread_id: str,
    document_ids: list[int] | None = None,
    *,
    route: AgentRoute | None = None,
    trace_id: str | None = None,
    observation_id: str | None = None,
    _hop_count: int = 0,
    use_mini: bool = False,
) -> AsyncIterator[tuple[str, bool, list[str]]]:
    with propagate_attributes(session_id=thread_id, user_id=get_user_id()) if thread_id else contextlib.nullcontext():
        """Stream tokens from the appropriate task agent."""
        from . import chat_agent, question_agent, retrieval_agent
        if _hop_count >= MAX_HANDOFFS:
            logger.warning(
                "Stream handoff limit reached (%d/%d) thread=%s; falling back to chat",
                _hop_count,
                MAX_HANDOFFS,
                thread_id,
            )
            async for item in chat_agent.stream(user_message, thread_id, document_ids,
                                                use_mini=use_mini):
                yield item
            return

        route = route or await classify_intent(user_message, document_ids, thread_id)

        trace_id = trace_id or new_id()
        plan = _build_execution_plan(
            route,
            trace_id=trace_id,
            observation_id=observation_id,
            collect_evidence=bool(document_ids) and _normalise_intent(route.intent) == "question",
        )
        step = plan.target_step
        route_intent = _normalise_intent(route.intent)
        await asyncio.to_thread(_write_router_trace, trace_id, thread_id, document_ids, route, user_id=get_user_id())

        if route_intent == "evaluation":
            result = await _run_evaluation_agent(
                thread_id,
                observation_id=step.observation_id,
                trace_id=step.trace_id,
            )
            await asyncio.to_thread(_write_router_trace, trace_id, thread_id, document_ids, route,
                                    end_time=datetime.now(timezone.utc), user_id=get_user_id())
            yield result.response, False, []
            yield "", True, []
            return

        last_sources: list[str] = []

        if route_intent == "research":
            result = await run_research_agent(
                user_message,
                thread_id,
                document_ids,
                observation_id=step.observation_id,
                trace_id=step.trace_id,
                original_intent=route.original_intent,
                resolved_intent=route.resolved_intent,
            )
            _fire_and_forget(_update_memory(
                route_intent, thread_id, get_user_id(), document_ids, user_message, result
            ))
            yield result.response, False, []
            last_sources = result.sources
        elif route_intent == "question":
            evidence_context = None
            evidence_sources: list[str] = []
            evidence_step = plan.evidence_step
            if evidence_step is not None:
                evidence = await retrieval_agent.answer(
                    user_message,
                    thread_id,
                    document_ids,
                    route_intent="question",
                    observation_id=evidence_step.observation_id,
                    trace_id=evidence_step.trace_id,
                    use_mini=use_mini,
                    original_intent=route.original_intent,
                    resolved_intent=route.resolved_intent,
                )
                evidence_context = evidence.response
                evidence_sources = evidence.sources
            async for item in question_agent.stream(
                user_message, thread_id, document_ids,
                observation_id=step.observation_id,
                trace_id=step.trace_id,
                use_mini=use_mini,
                original_intent=route.original_intent,
                resolved_intent=route.resolved_intent,
                evidence_context=evidence_context,
                evidence_sources=evidence_sources,
            ):
                token, is_done, sources = item
                if is_done == "interrupt":
                    yield item
                    return
                if is_done:
                    last_sources = sources
                else:
                    yield item
        elif route_intent == "retrieval":
            async for item in retrieval_agent.stream(
                user_message,
                thread_id,
                document_ids,
                route_intent=route_intent,
                observation_id=step.observation_id,
                trace_id=step.trace_id,
                use_mini=use_mini,
                original_intent=route.original_intent,
                resolved_intent=route.resolved_intent,
            ):
                token, is_done, sources = item
                if is_done == "interrupt":
                    yield item
                    return
                if is_done:
                    last_sources = sources
                else:
                    yield item
        else:
            async for item in chat_agent.stream(
                user_message, thread_id, document_ids,
                observation_id=step.observation_id,
                trace_id=step.trace_id,
                use_mini=use_mini,
                original_intent=route.original_intent,
                resolved_intent=route.resolved_intent,
            ):
                token, is_done, sources = item
                if is_done == "interrupt":
                    yield item
                    return
                if is_done:
                    last_sources = sources
                else:
                    yield item

        await asyncio.to_thread(_write_router_trace, trace_id, thread_id, document_ids, route,
                                end_time=datetime.now(timezone.utc), user_id=get_user_id())

        if plan.evaluate_after:
            _fire_and_forget(_run_evaluation_agent(
                thread_id,
                trace_id=plan.trace_id,
                exclude_observation_id=step.observation_id,
            ))

        yield "", True, last_sources
