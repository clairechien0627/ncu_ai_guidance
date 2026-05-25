"""Shared entry point for document research tasks."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Callable
from datetime import datetime, timezone

from langchain_openai import AzureChatOpenAI
from config import settings
from db import Document, db_session
from prompting.registry import resolve
from tools.rag_tool import set_query_expander_llm

from .research_graph import _hard_max_searches, _merge_stream_patch, _seed_keywords, final_rs_from_result, research_graph, get_or_build_research_graph  # noqa: F401
from .runtime_prompts import RESEARCH_BASE_STACK, research_base_stack_metadata
from .state import ResearchGraphState, ResearchState
from .task_planner import create_research_plan, fallback_research_plan
from ..types import AgentResult, AgentStatus
from ..no_tool_runner import write_agent_span

logger = logging.getLogger(__name__)

# Background tasks that should be awaited on shutdown.
_background_tasks: set[asyncio.Task] = set()

# Active RunControl objects for in-flight research graph runs.
# Populated by _run_graph_streaming; drained by request_all_drain() on shutdown.
_active_run_controls: set = set()


def request_all_drain(reason: str = "shutdown") -> None:
    """Signal all active research graph runs to stop at the next superstep boundary."""
    for ctrl in list(_active_run_controls):
        try:
            ctrl.request_drain(reason)
        except Exception:
            pass

# ── Public constants ─────────────────────────────────────────────────────────
RESEARCH_AGENT_NAME = "research"
SUMMARY_AGENT_NAME = "research"
RESEARCH_STACK_NAME = "research_runtime"


def trace_metadata(
    thread_id: str | None = None,
    document_ids: list[int] | None = None,
    *,
    agent_name: str = SUMMARY_AGENT_NAME,
    stack_name: str = RESEARCH_STACK_NAME,
) -> dict[str, str | int]:
    """Build trace metadata for research or summary runs."""
    from prompting.loader import load_stack as _load_stack

    def _prompt_key(tid: str | None, doc_ids: list[int] | None) -> str:
        doc_key = ",".join(str(d) for d in sorted(doc_ids or []))
        return f"{tid or ''}:{doc_key}"

    stack = _load_stack(stack_name, _prompt_key(thread_id, document_ids))
    return {
        "agent_name": agent_name,
        **stack.metadata(),
    }


RUNTIME_PROMPT_NAMES = (
    "task_planner",
    "research_scheduler",
    "research_planner",
    "research_reflector",
    "research_writer",
)


def _runtime_prompt_specs() -> list[dict[str, str]]:
    specs: list[dict[str, str]] = []
    for name in RUNTIME_PROMPT_NAMES:
        spec = resolve(name)
        specs.append({
            "name": spec.name,
            "base_name": spec.base_name,
            "source_name": spec.source_name,
            "version": spec.version,
        })
    return specs


def _compact_prompt_summary(specs: list[dict[str, str]]) -> str:
    return "、".join(
        f"{item['name']}({item['version']})"
        for item in specs
    )


def _parse_json_field(value):
    if not value:
        return None
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except Exception:
        return value


def _graph_runtime_metadata(metadata: dict) -> dict:
    keep = (
        "agent_name",
        "prompt_stack_name",
        "primary_prompt_json",
        "workflow_prompts_json",
        "prompt_stack_tokens",
        "base_prompt_name",
        "base_prompt_hash",
        "prompt_name",
        "prompt_version",
        "task_prompt_name",
        "task_prompt_hash",
        "document_id",
        "type",
        "research_effective_base_stack_name",
        "research_runtime_prompt_summary",
    )
    return {key: metadata[key] for key in keep if key in metadata}


def _llm() -> AzureChatOpenAI:
    return AzureChatOpenAI(
        azure_deployment=settings.azure_chat_deployment,
        azure_endpoint=settings.azure_openai_endpoint,
        api_key=settings.azure_openai_api_key.get_secret_value(),
        api_version=settings.azure_openai_api_version,
        temperature=0,
    )


def _utcnow() -> datetime:
    # SQLAlchemy expects naive datetimes; strip tzinfo to stay consistent with DB column type.
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _slot_label(state: dict, slot: str) -> str:
    for item in state.get("coverage_items", []) or []:
        if not isinstance(item, dict) or item.get("id") != slot:
            continue
        label = str(item.get("label") or item.get("name") or "").strip()
        if label:
            return label[:16]
    labels = {
        "motivation": "研究動機",
        "method": "研究方法",
        "results": "研究成果",
        "limitations": "研究限制",
        "research_motivation": "研究動機",
        "research_methods": "研究方法",
        "research_findings": "研究成果",
        "research_limitations": "研究限制",
    }
    return labels.get(slot, slot.replace("_", " ") if slot else "檢索項目")




async def _run_graph_streaming(
    initial_state: ResearchGraphState,
    graph_config: dict,
    on_stage: Callable[[str], None] | None,
    on_token: Callable[[str], None] | None = None,
    graph=None,
) -> ResearchGraphState:
    from langgraph.errors import GraphDrained
    from langgraph.runtime import RunControl

    state: dict = dict(initial_state)
    _graph = graph if graph is not None else research_graph
    control = RunControl()
    _active_run_controls.add(control)
    try:
        async for chunk in _graph.astream(
            initial_state,
            config=graph_config,
            stream_mode=["updates", "messages"],
            version="v2",
            durability="async",
            control=control,
        ):
            if chunk["type"] == "updates":
                for node_name, patch in chunk["data"].items():
                    if not isinstance(patch, dict):
                        continue
                    state = _merge_stream_patch(state, patch)
            elif chunk["type"] == "messages" and on_token:
                msg, metadata = chunk["data"]
                if msg.content and metadata.get("langgraph_node") == "writer":
                    on_token(msg.content)
    except GraphDrained:
        logger.info("research graph drained at superstep boundary (observation_id=%s)", state.get("observation_id"))
    finally:
        _active_run_controls.discard(control)
    return state


def _document_context(document_ids: list[int]) -> str:
    with db_session() as db:
        rows = db.query(Document.id, Document.filename, Document.abstract_text).filter(
            Document.id.in_(document_ids)
        ).all()
        return "\n\n".join(
            f"[{row.filename}]\n{row.abstract_text or ''}".strip()
            for row in rows
        )


async def _async_quality_check(*, observation_id: str, answer: str, document_ids: list[int]) -> None:
    """Non-blocking evaluation after high-value research output completes."""
    if not observation_id or not answer or len(answer.strip()) < 100:
        return
    try:
        from agents.evaluation_agent import evaluate_trace_by_observation_id

        result = await evaluate_trace_by_observation_id(observation_id)
        if result and result.overall < 2.5:
            logger.warning(
                "research evaluation below threshold (score=%.2f, observation_id=%s): %s",
                result.overall,
                observation_id,
                result.verdict,
            )
    except Exception as exc:
        logger.debug("_async_quality_check failed for observation_id=%s: %s", observation_id, exc)


async def _emit_stage(on_stage, msg: str) -> None:
    """Call on_stage, awaiting it if async."""
    if not on_stage:
        return
    try:
        result = on_stage(msg)
        if asyncio.iscoroutine(result):
            await result
    except Exception:
        pass


async def _plan_research(
    llm,
    question: str,
    research_mode: str,
    context: str,
    on_stage: Callable[[str], None] | None,
) -> tuple[str, list[dict], str, list[str], int]:
    """Run LLM planning; falls back to keyword plan on failure.

    Returns (task_goal, coverage_items, output_contract, coverage_ids, llm_call_count).
    """
    await _emit_stage(on_stage, "分析任務：建立檢索項目")
    try:
        plan = await create_research_plan(
            llm,
            question=question,
            task_context=research_mode,
            document_context=context,
        )
        plan_llm_calls = 1
    except Exception:
        await _emit_stage(on_stage, "任務規劃失敗：使用預設檢索項目")
        plan = fallback_research_plan(research_mode, question)
        plan_llm_calls = 0

    task_goal, coverage_items, output_contract = plan.as_state_parts()
    coverage_ids = [item["id"] for item in coverage_items]
    await _emit_stage(on_stage, f"任務規劃完成：{len(coverage_ids)} 個檢索項目")
    return task_goal, coverage_items, output_contract, coverage_ids, plan_llm_calls


def _build_initial_graph_state(
    *,
    question: str,
    document_ids: list[int],
    context: str,
    observation_id: str,
    thread_id: str,
    metadata: dict,
    max_searches: int,
    max_searches_per_slot: int,
    max_consecutive_no_new: int,
    min_evidence_per_slot: int,
    task_goal: str,
    coverage_items: list[dict],
    output_contract: str,
    coverage_ids: list[str],
    plan_llm_calls: int,
    started_at: datetime,
) -> "ResearchGraphState":
    """Build the initial LangGraph state dict from resolved plan and runtime params."""
    graph_metadata = _graph_runtime_metadata(metadata)
    required_count = len([i for i in coverage_items if i.get("required", True)]) or 1
    effective_max_searches = max(max_searches, required_count * max_searches_per_slot + 1)
    return {
        "question": question,
        "document_ids": document_ids,
        "document_context": context[:2000],
        "observation_id": observation_id,
        "thread_id": thread_id,
        "metadata": graph_metadata,
        "max_searches": effective_max_searches,
        "max_searches_per_slot": max_searches_per_slot,
        "max_consecutive_no_new": max_consecutive_no_new,
        "min_evidence_per_slot": min_evidence_per_slot,
        "task_goal": task_goal,
        "coverage_items": coverage_items,
        "output_contract": output_contract,
        "search_count": 0,
        "consecutive_no_new": 0,
        "known_keywords": _seed_keywords(context),
        "used_queries": [],
        "slot_status": {slot: "NOT_FILLED" for slot in coverage_ids},
        "evidence": {slot: [] for slot in coverage_ids},
        "evidence_details": {slot: [] for slot in coverage_ids},
        "sources": [],
        "last_reflection": "",
        "next_search_angle": "",
        "suggested_query_terms": [],
        "avoid_query_terms": [],
        "seen_chunk_keys": [],
        "used_query_keys": [],
        "scheduled_slot": None,
        "void_slot_attempts": {},
        "steps_json": [],
        "chunks_by_query_json": [],
        "trace_summary": {},
        "messages": [
            {"type": "system", "content": "研究流程由 runtime 控制：planner -> search_report -> reflector -> writer。"},
            {"type": "human", "content": question},
        ],
        "llm_call_count": plan_llm_calls,
        "started_at": started_at.isoformat(),
        "final_answer": "",
        "final_sources": [],
    }


async def run_research_task(
    *,
    question: str,
    thread_id: str,
    document_ids: list[int],
    observation_id: str,
    metadata: dict,
    research_mode: str = "document_extraction",
    on_stage: Callable[[str], None] | None = None,
    on_token: Callable[[str], None] | None = None,
    max_searches: int = 10,
    max_searches_per_slot: int = 7,
    max_consecutive_no_new: int = 4,
    min_evidence_per_slot: int | None = None,
    trace_id: str | None = None,
    bypass_cache: bool = False,
) -> AgentResult:
    # Document extraction requires at least 1 evidence note per slot before the
    # writer fires; ad-hoc research has no such constraint.
    if min_evidence_per_slot is None:
        min_evidence_per_slot = 1 if research_mode == "document_extraction" else 0
    started_at = _utcnow()

    if trace_id:
        await write_agent_span(
            observation_id=observation_id,
            trace_id=trace_id,
            thread_id=thread_id,
            parent_observation_id=trace_id,
            name="Research Agent",
            start_time=started_at,
            input_data={"messages": [{"role": "user", "content": question}]},
            extra_metadata={"agent_name": "research"},
        )

    metadata = dict(metadata)
    base_stack_meta = research_base_stack_metadata()
    base_prompts = _parse_json_field(base_stack_meta.get("prompt_stack_json")) or []
    runtime_prompts = _runtime_prompt_specs()
    metadata["research_effective_base_stack_name"] = RESEARCH_BASE_STACK
    metadata["research_effective_base_prompt_stack_json"] = base_stack_meta.get("prompt_stack_json")
    metadata["research_effective_system_prompt_json"] = json.dumps([*base_prompts, *runtime_prompts], ensure_ascii=False)
    metadata["research_runtime_prompt_json"] = json.dumps(runtime_prompts, ensure_ascii=False)
    metadata["research_runtime_prompt_summary"] = _compact_prompt_summary(runtime_prompts)

    llm = _llm()
    set_query_expander_llm(llm)
    context = _document_context(document_ids)

    task_goal, coverage_items, output_contract, coverage_ids, plan_llm_calls = await _plan_research(
        llm, question, research_mode, context, on_stage
    )
    initial_state = _build_initial_graph_state(
        question=question, document_ids=document_ids, context=context,
        observation_id=observation_id, thread_id=thread_id, metadata=metadata,
        max_searches=max_searches, max_searches_per_slot=max_searches_per_slot,
        max_consecutive_no_new=max_consecutive_no_new,
        min_evidence_per_slot=min_evidence_per_slot,
        task_goal=task_goal, coverage_items=coverage_items,
        output_contract=output_contract, coverage_ids=coverage_ids,
        plan_llm_calls=plan_llm_calls, started_at=started_at,
    )

    # ── Document research cache: pre-load if available ────────────────────────
    _env_bypass = os.environ.get("BYPASS_RESEARCH_CACHE", "").lower() in ("1", "true", "yes")
    if document_ids and not bypass_cache and not _env_bypass:
        try:
            from services.memory_service import load_document_research_cache
            cached = load_document_research_cache(document_ids, coverage_ids)
            if cached:
                initial_state["slot_status"].update(cached["slot_status"])
                for slot, notes in cached["evidence"].items():
                    if notes and slot in initial_state["evidence"]:
                        initial_state["evidence"][slot] = list(notes)
                for slot, details in cached["evidence_details"].items():
                    if details and slot in initial_state["evidence_details"]:
                        initial_state["evidence_details"][slot] = list(details)
                initial_state["known_keywords"] = list({
                    *initial_state["known_keywords"],
                    *cached["known_keywords"],
                })[:80]
                if cached["avoid_query_terms"]:
                    initial_state["avoid_query_terms"] = list(cached["avoid_query_terms"])[:30]
                if cached["sources"]:
                    initial_state["sources"] = list(cached["sources"])[:20]
                filled = sum(1 for s in cached["slot_status"].values() if s in ("FILLED", "EXHAUSTED"))
                if filled:
                    await _emit_stage(on_stage, f"載入研究快取：{filled} 個項目已完成")
        except Exception as exc:
            logger.debug("document research cache load failed: %s", exc)

    try:
        from agents.runner import get_checkpointer as _get_checkpointer, get_store as _get_store
        _cp = _get_checkpointer()
        _st = _get_store()
    except Exception:
        _cp = None
        _st = None
    _graph = get_or_build_research_graph(checkpointer=_cp, store=_st)

    tracer = None
    if trace_id:
        from services.trace_capture import LocalTracer
        tracer = LocalTracer(
            thread_id=thread_id,
            document_ids=document_ids,
            agent_name=metadata.get("agent_name") or "research",
            prompt_name=metadata.get("prompt_name"),
            prompt_version=metadata.get("prompt_version"),
            trace_id=trace_id,
            parent_observation_id=observation_id,
        )

    graph_config: dict = {
        "configurable": {
            "llm": llm,
            "on_stage": on_stage,
            "thread_id": observation_id,
        }
    }
    if tracer:
        graph_config["callbacks"] = [tracer]
    if plan_llm_calls == 0:
        # Task planning fell back to defaults → mark the entire graph run as WARNING
        graph_config["metadata"] = {
            "status": "WARNING",
            "status_message": "planning_fallback: using default research plan",
        }

    try:
        result = await _run_graph_streaming(initial_state, graph_config, on_stage, on_token, graph=_graph)
        answer = result["final_answer"]
        sources = result["final_sources"]
        final_state = final_rs_from_result(result)

        # ── Build structured coverage_result for downstream memory ────────────
        coverage_result = {
            slot: {
                "status": final_state.slot_status.get(slot, "NOT_FILLED"),
                "label": final_state.coverage_label(slot),
                "notes": [n[:150] for n in final_state.evidence.get(slot, [])[:2]],
            }
            for slot in final_state.coverage_ids()
        }

        # ── Document research cache: save if coverage is reasonable ───────────
        if document_ids:
            try:
                from services.memory_service import save_document_research_cache
                required = final_state.required_coverage_ids()
                useful = sum(
                    1 for s in required
                    if final_state.slot_status.get(s) in ("FILLED", "PARTIAL", "EXHAUSTED")
                )
                if required and useful >= max(1, len(required) // 2):
                    save_document_research_cache(
                        document_ids=document_ids,
                        coverage_ids=coverage_ids,
                        slot_status=dict(final_state.slot_status),
                        evidence={s: list(v[:8]) for s, v in final_state.evidence.items()},
                        evidence_details={s: list(v[:6]) for s, v in final_state.evidence_details.items()},
                        known_keywords=list(final_state.known_keywords[:60]),
                        avoid_query_terms=list(final_state.avoid_query_terms[:30]),
                        sources=list(final_state.sources[:20]),
                        search_count=final_state.search_count,
                    )
            except Exception as exc:
                logger.debug("document research cache save failed: %s", exc)

        ended_at = _utcnow()
        if trace_id:
            await write_agent_span(
                observation_id=observation_id,
                trace_id=trace_id,
                thread_id=thread_id,
                parent_observation_id=trace_id,
                name="Research Agent",
                start_time=started_at,
                end_time=ended_at,
                input_data={"messages": [{"role": "user", "content": question}]},
                output_data={"answer": answer, "sources": sources},
                extra_metadata={"agent_name": "research"},
            )

        # Fire-and-forget quality check — does not block the response.
        # Task is tracked in _background_tasks so main.py shutdown can await it.
        _task = asyncio.create_task(
            _async_quality_check(observation_id=observation_id, answer=answer, document_ids=document_ids)
        )
        _background_tasks.add(_task)
        _task.add_done_callback(_background_tasks.discard)

        _unfilled_gaps = [
            info.get("label") or slot
            for slot, info in coverage_result.items()
            if info.get("status") == "NOT_FILLED"
        ]
        _research_status = AgentStatus(
            completed=len(_unfilled_gaps) == 0,
            work_summary="完成多步驟研究分析。",
            gaps=_unfilled_gaps,
            agent_limitation="" if len(_unfilled_gaps) == 0 else "部分檢索項目未能找到足夠文件內容",
        )
        return AgentResult(
            response=answer, sources=sources,
            agent_name=str(metadata.get("agent_name") or "research"),
            prompt_name=str(metadata.get("prompt_name") or "research_writer"),
            prompt_version=str(metadata.get("prompt_version") or "unknown"),
            observation_id=observation_id,
            coverage_result=coverage_result,
            status=_research_status,
        )
    except Exception as exc:
        logger.exception("run_research_task failed (observation_id=%s)", observation_id)
        err_ended_at = _utcnow()
        if trace_id:
            await write_agent_span(
                observation_id=observation_id,
                trace_id=trace_id,
                thread_id=thread_id,
                parent_observation_id=trace_id,
                name="Research Agent",
                start_time=started_at,
                end_time=err_ended_at,
                input_data={"messages": [{"role": "user", "content": question}]},
                error=str(exc),
                extra_metadata={"agent_name": "research"},
            )
        raise


async def run_research_summary(
    *,
    question: str,
    thread_id: str,
    document_ids: list[int],
    observation_id: str,
    metadata: dict,
    on_stage: Callable[[str], None] | None = None,
    max_searches: int = 10,
    max_searches_per_slot: int = 7,
    max_consecutive_no_new: int = 4,
) -> AgentResult:
    return await run_research_task(
        question=question,
        thread_id=thread_id,
        document_ids=document_ids,
        observation_id=observation_id,
        metadata=metadata,
        research_mode="document_extraction",
        on_stage=on_stage,
        max_searches=max_searches,
        max_searches_per_slot=max_searches_per_slot,
        max_consecutive_no_new=max_consecutive_no_new,
    )
