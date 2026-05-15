"""Shared entry point for document research tasks."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from datetime import datetime, timezone

from langchain_openai import AzureChatOpenAI
from sqlalchemy.exc import IntegrityError

from config import settings
from db import Document, db_session, Trace
from prompting.registry import resolve
from tools.rag_tool import set_query_expander_llm

from .research_graph import _hard_max_searches, _merge_stream_patch, _seed_keywords, cbq_from_result, final_rs_from_result, research_graph, get_or_build_research_graph  # noqa: F401
from .runtime_prompts import RESEARCH_BASE_STACK, research_base_stack_metadata
from .state import ResearchGraphState, ResearchState, SearchStep
from .task_planner import create_research_plan, fallback_research_plan
from ..types import AgentResult

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
RESEARCH_AGENT_NAME = "research_agent"
SUMMARY_AGENT_NAME = "research_agent"
RESEARCH_STACK_NAME = "research_runtime"


def trace_metadata(
    thread_id: str | None = None,
    document_ids: list[int] | None = None,
    *,
    task_type: str = "document_extraction",
    route_intent: str | None = None,
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
        "task_type": task_type,
        "route_intent": route_intent,
        "agent_name": agent_name,
        **stack.metadata(),
    }


RUNTIME_PROMPT_NAMES = (
    "task_planner",
    "research_orchestrator",
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


def _prompt_trace_metadata(metadata: dict) -> dict:
    keys = (
        "prompt_stack_json",
        "primary_prompt_json",
        "workflow_prompts_json",
        "research_effective_system_prompt_json",
        "research_effective_base_prompt_stack_json",
        "research_runtime_prompt_json",
        "extract_step2_prompt_stack_json",
        "extract_step3_prompt_stack_json",
        "extract_step4_prompt_stack_json",
    )
    return {key: _parse_json_field(metadata.get(key)) for key in keys if metadata.get(key)}


def _graph_runtime_metadata(metadata: dict) -> dict:
    keep = (
        "task_type",
        "route_intent",
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
        "original_intent",
        "resolved_intent",
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


def _emit_graph_progress(
    on_stage: Callable[[str], None] | None,
    node_name: str,
    state: dict,
    patch: dict,
) -> None:
    if not on_stage:
        return
    try:
        if node_name == "scheduler":
            pending = patch.get("pending_slots") or []
            if pending:
                labels = ", ".join(
                    _slot_label(state, str(item.get("slot") or ""))
                    for item in pending
                    if isinstance(item, dict)
                )
                on_stage(f"排程搜尋：{labels}")
        elif node_name == "slot_executor":
            # Per-slot progress is emitted via on_stage inside _run_single_slot;
            # here we only emit a summary when the executor wave finishes.
            slot_status = patch.get("slot_status") or {}
            not_found = [s for s, v in slot_status.items() if v in ("NOT_FOUND", "OMITTED")]
            if not_found:
                labels = "、".join(_slot_label(state, s) for s in not_found)
                on_stage(f"無資料：{labels}")
        elif node_name == "writer":
            on_stage("產生研究整理")
    except Exception:
        logger.debug("failed to emit graph progress for node %s", node_name, exc_info=True)


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
                    _emit_graph_progress(on_stage, node_name, state, patch)
            elif chunk["type"] == "messages" and on_token:
                msg, metadata = chunk["data"]
                if msg.content and metadata.get("langgraph_node") == "writer":
                    on_token(msg.content)
    except GraphDrained:
        logger.info("research graph drained at superstep boundary (run_id=%s)", state.get("run_id"))
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


def _fallback_display_messages(
    state: ResearchState,
    chunks_by_query: list[tuple[SearchStep, list[dict]]],
) -> list[dict]:
    messages: list[dict] = [{"role": "human", "content": state.question}]
    for index, (step, chunks) in enumerate(chunks_by_query, start=1):
        call_id = f"research_search_{index}"
        messages.append({"role": "ai", "content": step.thought or step.planner_rationale})
        messages.append({
            "role": "ai_tool_call",
            "tool_calls": [{
                "tool": "search_report",
                "call_id": call_id,
                "args": {
                    "display_intent": step.display_intent,
                    "keyword_query": step.keyword_query,
                    "semantic_query": step.semantic_query,
                    "section_terms": step.section_terms,
                    "use_hyde": step.use_hyde,
                    "slot": step.slot,
                },
            }],
        })
        messages.append({
            "role": "tool",
            "tool": "search_report",
            "call_id": call_id,
            "chunks": [
                {
                    "filename": chunk.get("filename", ""),
                    "page": chunk.get("page"),
                    "content": str(chunk.get("content", ""))[:900],
                }
                for chunk in chunks[:4]
            ],
        })
        messages.append({
            "role": "ai",
            "content": f"Reflection for {step.slot}: {step.quality}. {step.note} Missing: {step.missing_gap}",
        })
    return messages


def _normalize_display_messages(messages: list[dict]) -> list[dict]:
    normalized: list[dict] = []
    for msg in messages:
        kind = msg.get("role") or msg.get("type")
        content = str(msg.get("content") or "")

        if kind == "system":
            normalized.append({"role": "system", "content": content})
        elif kind == "human":
            normalized.append({"role": "human", "content": content})
        elif kind == "ai":
            if content:
                normalized.append({"role": "ai", "content": content})
            tool_calls = msg.get("tool_calls") or []
            if tool_calls:
                normalized.append({
                    "role": "ai_tool_call",
                    "tool_calls": [
                        {
                            "tool": call.get("tool") or call.get("name") or "tool",
                            "call_id": call.get("call_id") or call.get("id") or "",
                            "args": call.get("args") or {},
                        }
                        for call in tool_calls
                    ],
                })
        elif kind == "ai_tool_call":
            normalized.append({"role": "ai_tool_call", "tool_calls": msg.get("tool_calls") or []})
        elif kind == "tool":
            raw = msg.get("raw") or content
            chunks = msg.get("chunks") if isinstance(msg.get("chunks"), list) else []
            if not chunks and raw:
                try:
                    payload = json.loads(raw)
                    chunks = [
                        {
                            "filename": chunk.get("filename", ""),
                            "page": chunk.get("page"),
                            "content": str(chunk.get("content", ""))[:900],
                        }
                        for chunk in payload.get("results", [])[:4]
                    ]
                except Exception:
                    chunks = []
            normalized.append({
                "role": "tool",
                "tool": msg.get("tool") or msg.get("name") or "tool",
                "call_id": msg.get("call_id") or msg.get("tool_call_id") or "",
                "chunks": chunks,
                "raw": raw,
            })
    return normalized


def _build_trace_events(
    normalized_messages: list[dict],
    steps: list[dict],
    question: str = "",
    answer: str = "",
) -> list[dict]:
    """Build structured trace events for the monitor timeline.

    Primary source is trace_summary.steps (always complete).
    Messages are only used to extract user question and writer answer if present.
    This avoids the bug where LangGraph state messages only contain the final message
    after chunks_by_query_json is cleared by the writer node.
    """
    if not steps:
        # Fallback to message-based parsing when no steps available (non-research traces)
        return _build_events_from_messages(normalized_messages)

    events: list[dict] = []

    # User message — from messages or question param
    user_content = question
    for msg in normalized_messages:
        if msg.get("role") == "human":
            user_content = msg.get("content", question)
            break
    events.append({
        "type": "user_message",
        "round": 0,
        "slot_id": None,
        "slot_label": None,
        "display_intent": None,
        "payload_summary": user_content[:120],
        "payload_debug": None,
    })

    # One cycle per step: tool_call_args → tool_result → reflector
    for step in steps:
        rnd = step.get("round", 0)
        slot_id = step.get("slot", "")
        slot_label = step.get("slot_label", "") or slot_id
        display_intent = step.get("display_intent", "")
        quality = step.get("quality", "")
        chunk_count = step.get("chunk_count", 0)
        kw = step.get("keyword_query", "")
        sem = step.get("semantic_query", "")
        use_hyde = step.get("use_hyde", False)
        note = step.get("note", "")
        missing_gap = step.get("missing_gap", "")
        updated_slots = step.get("updated_slots", [])

        events.append({
            "type": "tool_call_args",
            "round": rnd,
            "slot_id": slot_id,
            "slot_label": slot_label,
            "display_intent": display_intent,
            "payload_summary": kw[:80] if kw else display_intent,
            "payload_debug": {
                "keyword_query": kw,
                "semantic_query": sem,
                "section_terms": step.get("section_terms", []),
                "use_hyde": use_hyde,
            },
        })
        quality_label = {"USEFUL": "✓ 有用", "NOT_USEFUL": "△ 不夠用", "NO_RESULTS": "✕ 無結果"}.get(quality, quality)
        events.append({
            "type": "tool_result",
            "round": rnd,
            "slot_id": slot_id,
            "slot_label": slot_label,
            "display_intent": display_intent,
            "payload_summary": f"返回 {chunk_count} 筆　{quality_label}",
            "payload_debug": {
                "quality": quality,
                "chunk_count": chunk_count,
                "updated_slots": updated_slots,
            },
        })
        events.append({
            "type": "reflector_message",
            "round": rnd,
            "slot_id": slot_id,
            "slot_label": slot_label,
            "display_intent": display_intent,
            "payload_summary": note or missing_gap or "—",
            "payload_debug": {
                "note": note,
                "missing_gap": missing_gap,
                "next_search_angle": step.get("next_search_angle", ""),
                "updated_slots": updated_slots,
            },
        })

    # Writer event — from answer param or last AI message
    writer_content = answer
    if not writer_content:
        for msg in reversed(normalized_messages):
            if msg.get("role") == "ai" and msg.get("content"):
                writer_content = msg["content"]
                break
    events.append({
        "type": "writer_message",
        "round": len(steps),
        "slot_id": None,
        "slot_label": None,
        "display_intent": "產出最終答案",
        "payload_summary": writer_content[:200],
        "payload_debug": None,
    })
    return events


def _build_events_from_messages(normalized_messages: list[dict]) -> list[dict]:
    """Fallback: build events from message list (for non-research traces or legacy data)."""
    events: list[dict] = []
    n = len(normalized_messages)
    tool_count = 0
    for i, msg in enumerate(normalized_messages):
        role = msg.get("role")
        if role == "human":
            events.append({"type": "user_message", "round": 0, "slot_id": None, "slot_label": None,
                           "display_intent": None, "payload_summary": msg.get("content", "")[:120], "payload_debug": None})
        elif role == "ai_tool_call":
            tool_calls = msg.get("tool_calls") or []
            args = tool_calls[0].get("args", {}) if tool_calls else {}
            events.append({"type": "tool_call_args", "round": tool_count + 1, "slot_id": None, "slot_label": None,
                           "display_intent": args.get("display_intent", ""), "payload_summary": str(args)[:80], "payload_debug": args})
        elif role == "tool":
            events.append({"type": "tool_result", "round": tool_count + 1, "slot_id": None, "slot_label": None,
                           "display_intent": None, "payload_summary": f"返回 {len(msg.get('chunks') or [])} 筆", "payload_debug": None})
        elif role == "ai":
            is_last = all(normalized_messages[j].get("role") != "ai" for j in range(i + 1, n))
            prev = next((normalized_messages[j].get("role") for j in range(i - 1, -1, -1)
                         if normalized_messages[j].get("role") not in ("system",)), None)
            if is_last:
                events.append({"type": "writer_message", "round": tool_count, "slot_id": None, "slot_label": None,
                               "display_intent": "產出最終答案", "payload_summary": msg.get("content", "")[:200], "payload_debug": None})
            elif prev == "tool":
                events.append({"type": "reflector_message", "round": tool_count + 1, "slot_id": None, "slot_label": None,
                               "display_intent": None, "payload_summary": msg.get("content", "")[:200], "payload_debug": None})
                tool_count += 1
    return events


def _trace_display(
    state: ResearchState,
    answer: str,
    sources: list[str],
    chunks_by_query: list[tuple[SearchStep, list[dict]]],
    messages: list[dict] | None = None,
    metadata: dict | None = None,
    trace_summary: dict | None = None,
) -> dict:
    trace_messages = _normalize_display_messages(
        messages or _fallback_display_messages(state, chunks_by_query)
    )
    # Use trace_summary steps (which include slot_label) when available;
    # fall back to steps_json from state (no slot_label).
    steps_for_events: list[dict] = []
    if trace_summary and isinstance(trace_summary.get("steps"), list):
        steps_for_events = trace_summary["steps"]
    elif state.steps_json:
        steps_for_events = state.steps_json
    display = {
        "messages": trace_messages,
        "events": _build_trace_events(trace_messages, steps_for_events, question=state.question, answer=answer),
        "answer": answer,
        "sources": sources,
        "research_state": state.as_prompt_dict(),
    }
    if trace_summary:
        display["trace_summary"] = trace_summary
    if metadata:
        display["prompt_metadata"] = _prompt_trace_metadata(metadata)
    return display


def _write_trace(
    *,
    run_id: str,
    thread_id: str,
    document_ids: list[int],
    metadata: dict,
    started_at: datetime,
    ended_at: datetime,
    state: ResearchState,
    answer: str,
    sources: list[str],
    chunks_by_query: list[tuple[SearchStep, list[dict]]],
    llm_call_count: int,
    messages: list[dict] | None = None,
    trace_summary: dict | None = None,
    error: str | None = None,
    parent_run_id: str | None = None,
) -> None:
    db = None
    try:
        with db_session() as db:
            display = _trace_display(state, answer, sources, chunks_by_query, messages, metadata, trace_summary)

            # Upsert pattern: create the row if it doesn't exist, update otherwise.
            # The try/except handles the rare concurrent-insert race on run_id.
            trace = db.query(Trace).filter(Trace.run_id == run_id).first()
            if trace is None:
                trace = Trace(run_id=run_id, run_type="chain", name="research_agent")
                db.add(trace)
                try:
                    db.flush()
                except IntegrityError:
                    db.rollback()
                    trace = db.query(Trace).filter(Trace.run_id == run_id).first()

            trace.parent_run_id = parent_run_id
            trace.inputs = json.dumps(
                {
                    "messages": [{"type": "human", "content": state.question}],
                    "task_goal": state.task_goal,
                    "output_contract": state.output_contract,
                    "coverage_items": state.coverage_items,
                    "initial_context": state.document_context[:2000],
                },
                ensure_ascii=False,
            )
            trace.outputs = json.dumps(
                {
                    "answer": answer,
                    "sources": sources,
                    "prompt_metadata": display.get("prompt_metadata", {}),
                },
                ensure_ascii=False,
            )
            trace.error = error
            trace.start_time = started_at
            trace.end_time = ended_at
            trace.thread_id = thread_id
            trace.document_ids = json.dumps(document_ids)
            trace.display = json.dumps(display, ensure_ascii=False)
            trace.task_type = metadata.get("task_type")
            trace.route_intent = metadata.get("route_intent")
            trace.agent_name = metadata.get("agent_name")
            trace.prompt_name = metadata.get("prompt_name")
            trace.prompt_version = metadata.get("prompt_version")
            trace.base_prompt_name = metadata.get("base_prompt_name")
            trace.task_prompt_name = metadata.get("task_prompt_name")
            trace.quality_prompt_name = metadata.get("quality_prompt_name")
            trace.base_prompt_hash = metadata.get("base_prompt_hash")
            trace.task_prompt_hash = metadata.get("task_prompt_hash")
            trace.quality_prompt_hash = metadata.get("quality_prompt_hash")
            trace.prompt_stack_name = metadata.get("prompt_stack_name")
            trace.prompt_stack_json = metadata.get("prompt_stack_json")
            trace.primary_prompt_json = metadata.get("primary_prompt_json")
            trace.workflow_prompts_json = metadata.get("workflow_prompts_json")
            trace.prompt_stack_tokens = metadata.get("prompt_stack_tokens")
            trace.tool_count = state.search_count
            trace.llm_call_count = llm_call_count
            trace.original_intent = metadata.get("original_intent")
            trace.resolved_intent = metadata.get("resolved_intent")
            db.commit()
    except Exception:
        if db is not None:
            db.rollback()
        raise


async def _async_quality_check(*, run_id: str, answer: str, document_ids: list[int]) -> None:
    """Non-blocking evaluation after high-value research output completes."""
    if not run_id or not answer or len(answer.strip()) < 100:
        return
    try:
        from agents.evaluation_agent import evaluate_trace_by_run_id

        result = await evaluate_trace_by_run_id(run_id)
        if result and result.overall < 2.5:
            logger.warning(
                "research evaluation below threshold (score=%.2f, run_id=%s): %s",
                result.overall,
                run_id,
                result.verdict,
            )
    except Exception as exc:
        logger.debug("_async_quality_check failed for run_id=%s: %s", run_id, exc)


async def _plan_research(
    llm,
    question: str,
    route_intent: str | None,
    task_type: str,
    context: str,
    on_stage: Callable[[str], None] | None,
) -> tuple[str, list[dict], str, list[str], int]:
    """Run LLM planning; falls back to keyword plan on failure.

    Returns (task_goal, coverage_items, output_contract, coverage_ids, llm_call_count).
    """
    if on_stage:
        on_stage("分析任務：建立檢索項目")
    try:
        plan = await create_research_plan(
            llm,
            question=question,
            task_context=route_intent or task_type,
            document_context=context,
        )
        plan_llm_calls = 1
    except Exception:
        if on_stage:
            on_stage("任務規劃失敗：使用預設檢索項目")
        plan = fallback_research_plan(route_intent or task_type, question)
        plan_llm_calls = 0

    task_goal, coverage_items, output_contract = plan.as_state_parts()
    coverage_ids = [item["id"] for item in coverage_items]
    if on_stage:
        on_stage(f"任務規劃完成：{len(coverage_ids)} 個檢索項目")
    return task_goal, coverage_items, output_contract, coverage_ids, plan_llm_calls


def _build_initial_graph_state(
    *,
    question: str,
    document_ids: list[int],
    context: str,
    run_id: str,
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
        "run_id": run_id,
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
        "pending_slots": [],
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
    run_id: str,
    metadata: dict,
    task_type: str = "document_extraction",
    route_intent: str | None = None,
    on_stage: Callable[[str], None] | None = None,
    on_token: Callable[[str], None] | None = None,
    max_searches: int = 10,
    max_searches_per_slot: int = 7,
    max_consecutive_no_new: int = 4,
    min_evidence_per_slot: int | None = None,
    parent_run_id: str | None = None,
) -> AgentResult:
    # Document extraction requires at least 1 evidence note per slot before the
    # writer fires; ad-hoc research has no such constraint.
    if min_evidence_per_slot is None:
        min_evidence_per_slot = 1 if task_type == "document_extraction" else 0
    started_at = _utcnow()

    metadata = dict(metadata)
    metadata.setdefault("task_type", task_type)
    metadata.setdefault("route_intent", route_intent)
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
        llm, question, route_intent, task_type, context, on_stage
    )
    initial_state = _build_initial_graph_state(
        question=question, document_ids=document_ids, context=context,
        run_id=run_id, thread_id=thread_id, metadata=metadata,
        max_searches=max_searches, max_searches_per_slot=max_searches_per_slot,
        max_consecutive_no_new=max_consecutive_no_new,
        min_evidence_per_slot=min_evidence_per_slot,
        task_goal=task_goal, coverage_items=coverage_items,
        output_contract=output_contract, coverage_ids=coverage_ids,
        plan_llm_calls=plan_llm_calls, started_at=started_at,
    )

    # ── Document research cache: pre-load if available ────────────────────────
    if document_ids:
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
                if filled and on_stage:
                    try:
                        on_stage(f"載入研究快取：{filled} 個項目已完成")
                    except Exception:
                        pass
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
    graph_config = {
        "configurable": {
            "llm": llm,
            "on_stage": None,
            "thread_id": run_id,
        }
    }

    try:
        result = await _run_graph_streaming(initial_state, graph_config, on_stage, on_token, graph=_graph)
        answer = result["final_answer"]
        sources = result["final_sources"]
        final_state = final_rs_from_result(result)
        chunks_by_query = cbq_from_result(result)
        messages = list(result.get("messages", []))

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

        _write_trace(
            run_id=run_id, thread_id=thread_id, document_ids=document_ids,
            metadata=metadata, started_at=started_at, ended_at=_utcnow(),
            state=final_state, answer=answer, sources=sources,
            chunks_by_query=chunks_by_query, llm_call_count=result["llm_call_count"],
            messages=messages, trace_summary=result.get("trace_summary") or None,
            parent_run_id=parent_run_id,
        )

        # Fire-and-forget quality check — does not block the response.
        # Task is tracked in _background_tasks so main.py shutdown can await it.
        _task = asyncio.create_task(
            _async_quality_check(run_id=run_id, answer=answer, document_ids=document_ids)
        )
        _background_tasks.add(_task)
        _task.add_done_callback(_background_tasks.discard)

        return AgentResult(
            response=answer, sources=sources,
            task_type=str(metadata.get("task_type") or task_type),
            route_intent=metadata.get("route_intent"),
            agent_name=str(metadata.get("agent_name") or "research_agent"),
            prompt_name=str(metadata.get("prompt_name") or "research_writer"),
            prompt_version=str(metadata.get("prompt_version") or "unknown"),
            trace_run_id=run_id,
            coverage_result=coverage_result,
        )
    except Exception as exc:
        logger.exception("run_research_task failed (run_id=%s)", run_id)
        err_state = ResearchState(
            question=question, document_ids=document_ids, document_context=context,
            task_goal=task_goal, coverage_items=coverage_items,
            output_contract=output_contract,
            slot_status={slot: "NOT_FILLED" for slot in coverage_ids},
            evidence={slot: [] for slot in coverage_ids},
        )
        _write_trace(
            run_id=run_id, thread_id=thread_id, document_ids=document_ids,
            metadata=metadata, started_at=started_at, ended_at=_utcnow(),
            state=err_state, answer="", sources=[], chunks_by_query=[],
            llm_call_count=0, error=str(exc), parent_run_id=parent_run_id,
        )
        raise


async def run_research_summary(
    *,
    question: str,
    thread_id: str,
    document_ids: list[int],
    run_id: str,
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
        run_id=run_id,
        metadata=metadata,
        task_type="document_extraction",
        route_intent=None,
        on_stage=on_stage,
        max_searches=max_searches,
        max_searches_per_slot=max_searches_per_slot,
        max_consecutive_no_new=max_consecutive_no_new,
    )
