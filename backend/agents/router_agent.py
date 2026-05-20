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

from .types import AgentRoute, AgentResult, AgentStatus
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
                "agent_name": route.agent_name,
                "document_ids": document_ids,
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
                        agent_name="router_agent",
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
VALID_AGENTS = {"chat_agent", "retrieval_agent", "research_agent", "question_agent", "evaluation_agent"}

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


class RouterDecision(BaseModel):
    agent_name: str = Field(
        description="Exact agent to invoke: chat_agent | retrieval_agent | research_agent | question_agent | evaluation_agent"
    )
    evaluate_after: bool = Field(default=False, description="True only when user simultaneously asks a question AND explicitly requests quality verification of the answer")
    reason: str = Field(default="", description="One sentence explaining the routing decision")


@dataclass(frozen=True)
class ExecutionStep:
    """One router-owned agent invocation."""

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



def _allows_background_evaluation(agent_name: str, has_docs: bool) -> bool:
    """Deterministic gate for router-triggered background evaluation.

    Explicit evaluation requests route directly to ``evaluation_agent``.  The
    background ``evaluate_after`` path is only for document-grounded task-agent
    outputs where an automatic quality pass is worth the extra cost.
    Extraction quality is handled separately by ``summary_quality``.
    """
    return has_docs and agent_name in {"research_agent", "retrieval_agent", "question_agent"}


def _normalise_decision(decision: RouterDecision, has_docs: bool) -> RouterDecision:
    """Sanitise LLM output: fix invalid agent names and enforce doc-gating rules."""
    agent_name = decision.agent_name if decision.agent_name in VALID_AGENTS else "chat_agent"
    if agent_name in {"research_agent", "retrieval_agent"} and not has_docs:
        agent_name = "chat_agent"
    evaluate_after = bool(decision.evaluate_after and _allows_background_evaluation(agent_name, has_docs))
    return RouterDecision(agent_name=agent_name, evaluate_after=evaluate_after, reason=decision.reason)


def _primary_prompt(stack_name: str, base_name: str, thread_id: str | None, document_ids: list[int] | None):
    stack = load_stack(stack_name, _prompt_key(thread_id, document_ids))
    return next((p for p in stack.prompts if p.base_name == base_name), stack.prompts[-1])


def _route_for_agent(
    agent_name: str,
    document_ids: list[int] | None = None,
    thread_id: str | None = None,
    *,
    evaluate_after: bool = False,
) -> AgentRoute:
    """Map agent_name to AgentRoute."""
    if agent_name not in VALID_AGENTS:
        agent_name = "chat_agent"
    if agent_name == "research_agent":
        prompt = _primary_prompt("research_runtime", "research_writer", thread_id, document_ids)
        return AgentRoute(agent_name, prompt.name, prompt.version, evaluate_after=evaluate_after)
    if agent_name == "question_agent":
        return AgentRoute(agent_name, "question_skill", prompt_version("question_skill"), evaluate_after=evaluate_after)
    if agent_name == "retrieval_agent":
        return AgentRoute(agent_name, "retrieval_capability", prompt_version("retrieval_capability"), evaluate_after=evaluate_after)
    if agent_name == "evaluation_agent":
        return AgentRoute(agent_name, "evaluation_agent", prompt_version("evaluation_agent"), evaluate_after=False)
    return AgentRoute("chat_agent", "chat_mode", prompt_version("chat_mode"), evaluate_after=False)


def _fetch_document_context(document_ids: list[int]) -> list[dict]:
    """Fetch filename + abstract_text for selected docs to inform routing. Runs in thread."""
    from db import db_session, Document as DocModel
    with db_session() as db:
        docs = (
            db.query(DocModel.id, DocModel.filename, DocModel.abstract_text)
            .filter(DocModel.id.in_(document_ids))
            .all()
        )
    return [
        {
            "id": d.id,
            "filename": d.filename,
            "abstract": (d.abstract_text or "")[:600],
        }
        for d in docs
    ]


def _build_execution_plan(
    route: AgentRoute,
    *,
    trace_id: str,
    observation_id: str | None = None,
    collect_evidence: bool = False,
) -> ExecutionPlan:
    """Build the router-owned execution plan for a request."""
    agent_name = route.agent_name
    steps: list[ExecutionStep] = []
    if collect_evidence and agent_name == "question_agent":
        steps.append(ExecutionStep(
            agent_name="retrieval_agent",
            observation_id=new_id(),
            trace_id=trace_id,
            kind="evidence_collection",
            reason="question_requires_document_evidence",
        ))
    steps.append(ExecutionStep(
        agent_name=agent_name,
        observation_id=observation_id or new_id(),
        trace_id=trace_id,
        kind="primary",
        reason=f"agent={agent_name}",
    ))
    if route.compose_after and agent_name not in {"chat_agent", "evaluation_agent"}:
        steps.append(ExecutionStep(
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


async def _orchestrate(
    message: str,
    has_docs: bool,
    document_ids: list[int] | None = None,
    previous_agent: str | None = None,
    is_followup: bool = False,
    agent_status: AgentStatus | None = None,
) -> RouterDecision:
    """Orchestrator LLM: decides which agent to invoke with full document context."""
    from observability import ainvoke_traced_generation

    stack = load_stack("router_default")
    system = get_prompt(ROUTER_PROMPT_NAME)

    doc_context: list[dict] = []
    if has_docs and document_ids:
        doc_context = await asyncio.to_thread(_fetch_document_context, document_ids)

    user = {
        "message": message,
        "has_documents": has_docs,
        "document_context": doc_context,
        "previous_agent": previous_agent,
        "is_followup_signal": is_followup,
        "agent_status": {
            "completed": agent_status.completed,
            "work_summary": agent_status.work_summary,
            "gaps": agent_status.gaps,
            "agent_limitation": agent_status.agent_limitation,
        } if agent_status else None,
        "available_agents": [
            "chat_agent", "retrieval_agent", "research_agent", "question_agent", "evaluation_agent",
        ],
        "response_format": {
            "agent_name": "chat_agent|retrieval_agent|research_agent|question_agent|evaluation_agent",
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
                "agent_name": "router_agent",
                **stack.metadata(),
            },
        )
        return _normalise_decision(decision, has_docs)
    except Exception as exc:
        logger.warning("Orchestrator LLM failed; using conservative fallback: %s", exc)
        fallback = "retrieval_agent" if has_docs else "chat_agent"
        return RouterDecision(agent_name=fallback, evaluate_after=False, reason="fallback")


async def classify_intent(
    message: str,
    document_ids: list[int] | None = None,
    thread_id: str | None = None,
    previous_agent_name: str | None = None,
) -> AgentRoute:
    with propagate_attributes(session_id=thread_id, user_id=get_user_id()) if thread_id else contextlib.nullcontext():
        """Route a request via Orchestrator LLM with full document context.

        previous_agent_name: agent_name used in the last turn (from TraceV2 metadata).
        Passed to the orchestrator as a follow-up hint.
        """
        has_docs = bool(document_ids)
        is_followup = _looks_like_followup(message)
        decision = await _orchestrate(
            message,
            has_docs,
            document_ids,
            previous_agent=previous_agent_name,
            is_followup=is_followup,
        )
        return _route_for_agent(
            decision.agent_name, document_ids, thread_id, evaluate_after=decision.evaluate_after
        )


async def _update_memory(
    agent_name: str,
    thread_id: str,
    user_id: str | None,
    document_ids: list[int] | None,
    question: str,
    result,
) -> None:
    """Fire-and-forget: write memory per agent_name policy via agent_memory contract."""
    from services.agent_memory import record_agent_memory
    await record_agent_memory(
        agent_name=agent_name,
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
            agent_name="evaluation_agent",
            prompt_name="evaluation_agent",
            prompt_version=prompt_version("evaluation_agent"),
            observation_id=observation_id,
            status=AgentStatus(completed=True, work_summary="完成品質評估。"),
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
    bypass_cache: bool = False,
) -> AgentResult:
    with propagate_attributes(session_id=thread_id, user_id=get_user_id()) if thread_id else contextlib.nullcontext():
        from .research import run_research_task

        stack = load_stack("research_runtime", _prompt_key(thread_id, document_ids))
        metadata = {
            "task_type": "research_task",
            "agent_name": "research_agent",
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
            collect_evidence=bool(document_ids) and route.agent_name == "question_agent",
        )
        step = plan.target_step
        await asyncio.to_thread(_write_router_trace, trace_id, thread_id, document_ids, route,
                                user_message=user_message, user_id=get_user_id())

        try:
            if route.agent_name == "evaluation_agent":
                result = await _run_evaluation_agent(
                    thread_id,
                    observation_id=step.observation_id,
                    trace_id=step.trace_id,
                )
            elif route.agent_name == "research_agent":
                result = await run_research_agent(
                    user_message,
                    thread_id,
                    document_ids,
                    observation_id=step.observation_id,
                    trace_id=step.trace_id,
                )
            elif route.agent_name == "question_agent":
                evidence_context = None
                evidence_sources: list[str] = []
                evidence_step = plan.evidence_step
                if evidence_step is not None:
                    evidence = await retrieval_agent.answer(
                        user_message,
                        thread_id,
                        document_ids,
                        observation_id=evidence_step.observation_id,
                        trace_id=evidence_step.trace_id,
                        use_mini=use_mini,
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
                    evidence_context=evidence_context,
                    evidence_sources=evidence_sources,
                )
            elif route.agent_name == "retrieval_agent":
                result = await retrieval_agent.answer(
                    user_message,
                    thread_id,
                    document_ids,
                    observation_id=step.observation_id,
                    trace_id=step.trace_id,
                    use_mini=use_mini,
                )
                # Re-routing: if retrieval did not complete and docs are available,
                # ask orchestrator whether to escalate to a different agent.
                if not result.status.completed and document_ids and _hop_count + 1 < MAX_HANDOFFS:
                    escalation_decision = await _orchestrate(
                        user_message,
                        has_docs=bool(document_ids),
                        document_ids=document_ids,
                        previous_agent=route.agent_name,
                        agent_status=result.status,
                    )
                    if escalation_decision.agent_name != route.agent_name:
                        escalation_route = _route_for_agent(
                            escalation_decision.agent_name,
                            document_ids,
                            thread_id,
                            evaluate_after=escalation_decision.evaluate_after,
                        )
                        return await route_agent_message(
                            user_message,
                            thread_id,
                            document_ids,
                            route=escalation_route,
                            trace_id=trace_id,
                            _hop_count=_hop_count + 1,
                            use_mini=use_mini,
                        )
            else:
                result = await chat_agent.answer(
                    user_message,
                    thread_id,
                    document_ids,
                    observation_id=step.observation_id,
                    trace_id=step.trace_id,
                    use_mini=use_mini,
                )
        except Exception as exc:
            await asyncio.to_thread(
                _write_router_trace, trace_id, thread_id, document_ids, route,
                end_time=datetime.now(timezone.utc), error=str(exc), user_id=get_user_id(),
            )
            raise

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

        if route.agent_name == "research_agent":
            _fire_and_forget(_update_memory(
                "research", thread_id, get_user_id(), document_ids, user_message, result
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
            collect_evidence=bool(document_ids) and route.agent_name == "question_agent",
        )
        step = plan.target_step
        await asyncio.to_thread(_write_router_trace, trace_id, thread_id, document_ids, route, user_id=get_user_id())

        if route.agent_name == "evaluation_agent":
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

        if route.agent_name == "research_agent":
            result = await run_research_agent(
                user_message,
                thread_id,
                document_ids,
                observation_id=step.observation_id,
                trace_id=step.trace_id,
            )
            _fire_and_forget(_update_memory(
                "research", thread_id, get_user_id(), document_ids, user_message, result
            ))
            yield result.response, False, []
            last_sources = result.sources
        elif route.agent_name == "question_agent":
            evidence_context = None
            evidence_sources: list[str] = []
            evidence_step = plan.evidence_step
            if evidence_step is not None:
                evidence = await retrieval_agent.answer(
                    user_message,
                    thread_id,
                    document_ids,
                    observation_id=evidence_step.observation_id,
                    trace_id=evidence_step.trace_id,
                    use_mini=use_mini,
                )
                evidence_context = evidence.response
                evidence_sources = evidence.sources
            async for item in question_agent.stream(
                user_message, thread_id, document_ids,
                observation_id=step.observation_id,
                trace_id=step.trace_id,
                use_mini=use_mini,
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
        elif route.agent_name == "retrieval_agent":
            _retrieval_result = await retrieval_agent.answer(
                user_message,
                thread_id,
                document_ids,
                observation_id=step.observation_id,
                trace_id=step.trace_id,
                use_mini=use_mini,
            )
            # Re-routing: if retrieval did not complete and docs are available,
            # ask orchestrator whether to escalate to a different agent.
            if not _retrieval_result.status.completed and document_ids and _hop_count + 1 < MAX_HANDOFFS:
                _escalation_decision = await _orchestrate(
                    user_message,
                    has_docs=bool(document_ids),
                    document_ids=document_ids,
                    previous_agent=route.agent_name,
                    agent_status=_retrieval_result.status,
                )
                if _escalation_decision.agent_name != route.agent_name:
                    _escalation_route = _route_for_agent(
                        _escalation_decision.agent_name,
                        document_ids,
                        thread_id,
                        evaluate_after=_escalation_decision.evaluate_after,
                    )
                    async for item in route_agent_stream(
                        user_message,
                        thread_id,
                        document_ids,
                        route=_escalation_route,
                        trace_id=trace_id,
                        _hop_count=_hop_count + 1,
                        use_mini=use_mini,
                    ):
                        yield item
                    return
            yield _retrieval_result.response, False, []
            last_sources = _retrieval_result.sources
        else:
            async for item in chat_agent.stream(
                user_message, thread_id, document_ids,
                observation_id=step.observation_id,
                trace_id=step.trace_id,
                use_mini=use_mini,
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
