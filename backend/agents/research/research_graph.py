"""LangGraph research graph — routing, error handlers, graph construction, public API."""

from __future__ import annotations

import logging
from typing import Literal

from langgraph.errors import NodeError
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, RetryPolicy, Send, TimeoutPolicy

from .graph_nodes import (
    batch_complete_node,
    orchestrator_node,
    slot_worker_node,  # re-exported for tests
    verification_node,
    writer_node,
)
from .graph_utils import (
    _force_next_angle_if_stalled,
    _graph_to_rs,
    _hard_max_searches,
    _merge_stream_patch,
    _next_coverage_slot,
    _per_slot_cap,
    _ready_for_verification,
    _seed_keywords,
    _slot_search_counts,
    _trace_summary,
    _verification_display_intent,
)
from .state import DEFAULT_SUMMARY_COVERAGE, ResearchGraphState, ResearchState, SearchStep, WorkerState
from .writer import _fallback_writeup

logger = logging.getLogger(__name__)

# Re-export graph_utils symbols used by agent.py and tests
__all__ = [
    "_force_next_angle_if_stalled",
    "_hard_max_searches",
    "_merge_stream_patch",
    "_next_coverage_slot",
    "_ready_for_verification",
    "_seed_keywords",
    "_verification_display_intent",
    "cbq_from_result",
    "final_rs_from_result",
    "get_or_build_research_graph",
    "research_graph",
]


# ── Routing ───────────────────────────────────────────────────────────────────

def _required_ids_from_state(state: ResearchGraphState) -> list[str]:
    """Extract required coverage IDs directly from state without building ResearchState."""
    items = state.get("coverage_items", [])
    ids = [str(item.get("id")) for item in items if item.get("required", True) and item.get("id")]
    if not ids:
        ids = [str(item.get("id")) for item in items if item.get("id")]
    return ids or list(t["id"] for t in DEFAULT_SUMMARY_COVERAGE)


def should_continue(state: ResearchGraphState) -> Literal["orchestrator", "writer", "verification"]:
    """Route after each superstep. Reads state directly — no ResearchState construction."""
    if state["search_count"] >= _hard_max_searches(state):
        return "writer"

    required = _required_ids_from_state(state)
    slot_status: dict = state.get("slot_status", {})
    counts = _slot_search_counts(state)
    per_slot = _per_slot_cap(state)
    min_ev = state.get("min_evidence_per_slot", 0)

    # Done: verification complete + every required slot settled
    if state["verification_done"]:
        all_settled = all(slot_status.get(s, "NOT_FILLED") in ("FILLED", "PARTIAL", "EXHAUSTED") for s in required)
        if all_settled:
            if min_ev <= 0:
                return "writer"
            sufficient = all(
                slot_status.get(s) == "EXHAUSTED"
                or len(state.get("evidence", {}).get(s, [])) >= min_ev
                for s in required
            )
            if sufficient:
                return "writer"

    # Verification: all covered + every slot searched at least once
    if not state["verification_done"]:
        all_covered = all(slot_status.get(s) in ("FILLED", "PARTIAL", "EXHAUSTED") for s in required)
        all_searched = all(counts.get(s, 0) > 0 for s in required)
        if state["search_count"] >= 2 and all_covered and all_searched:
            return "verification"

    # Stalled: consecutive-no-new limit hit + all slots searched at least once
    if state["consecutive_no_new"] >= state.get("max_consecutive_no_new", 4):
        if all(counts.get(s, 0) > 0 for s in required):
            return "writer"

    # No candidates remain
    candidates = [
        s for s in required
        if slot_status.get(s) not in ("FILLED", "EXHAUSTED") and counts.get(s, 0) < per_slot
    ]
    if not candidates:
        return "writer"

    return "orchestrator"


def assign_workers(state: ResearchGraphState):
    batch_plan = state.get("batch_plan", [])
    if not batch_plan:
        return "writer"
    return [
        Send(
            "slot_worker",
            {
                **state,
                "worker_slot": item["slot"],
                "worker_hint": item.get("hint", ""),
            },
        )
        for item in batch_plan
    ]


# ── Fault-tolerance error handlers ───────────────────────────────────────────
# Each handler runs after all retries are exhausted. They return a minimal state
# update so the graph continues gracefully rather than crashing the whole run.

def _orchestrator_error_handler(state: ResearchGraphState, error: NodeError) -> dict:
    logger.warning("orchestrator exhausted retries (%s); clearing batch_plan", error.error)
    return {"batch_plan": []}


def _slot_worker_error_handler(state: WorkerState, error: NodeError) -> dict:
    slot = state.get("worker_slot", "unknown")
    logger.warning("slot_worker exhausted retries for slot=%s (%s)", slot, error.error)
    return {
        "steps_json": [{
            "slot": slot,
            "query": slot,
            "display_intent": f"錯誤：{type(error.error).__name__}",
            "quality": "ERROR",
            "chunk_count": 0,
            "updated_slots": [],
            "missing_gap": str(error.error)[:120],
            "next_search_angle": "",
            "is_verification": False,
        }],
        "slot_status": {slot: "EXHAUSTED"},
        "search_count": 1,
        "_batch_evidence_flags": [False],
    }


def _verification_error_handler(state: ResearchGraphState, error: NodeError) -> Command:
    logger.warning("verification exhausted retries (%s); routing to writer", error.error)
    return Command(update={"verification_done": True}, goto="writer")


def _writer_error_handler(state: ResearchGraphState, error: NodeError) -> dict:
    logger.warning("writer exhausted retries (%s); using evidence fallback", error.error)
    rs = _graph_to_rs(state)
    writeup = _fallback_writeup(rs)
    final_state = dict(state)
    final_state.update({"final_answer": writeup.answer, "final_sources": writeup.sources})
    return {
        "final_answer": writeup.answer,
        "final_sources": writeup.sources,
        "messages": [{"type": "ai", "content": writeup.answer, "sources": writeup.sources}],
        "trace_summary": _trace_summary(final_state),
        "llm_call_count": 0,
    }


# ── Graph construction ────────────────────────────────────────────────────────

def _build(checkpointer=False, store=None) -> object:
    builder = StateGraph(ResearchGraphState)
    builder.add_node(
        "orchestrator", orchestrator_node,
        retry_policy=RetryPolicy(max_attempts=2, initial_interval=1.0),
        timeout=TimeoutPolicy(run_timeout=90),
        error_handler=_orchestrator_error_handler,
    )
    builder.add_node(
        "slot_worker", slot_worker_node,
        retry_policy=RetryPolicy(max_attempts=3, initial_interval=1.0),
        timeout=TimeoutPolicy(run_timeout=180),
        error_handler=_slot_worker_error_handler,
    )
    builder.add_node("batch_complete", batch_complete_node)
    builder.add_node(
        "verification", verification_node,
        retry_policy=RetryPolicy(max_attempts=2, initial_interval=1.0),
        timeout=TimeoutPolicy(run_timeout=180),
        error_handler=_verification_error_handler,
    )
    builder.add_node(
        "writer", writer_node,
        retry_policy=RetryPolicy(max_attempts=2, initial_interval=2.0),
        timeout=TimeoutPolicy(run_timeout=240),
        error_handler=_writer_error_handler,
    )
    builder.add_edge(START, "orchestrator")
    builder.add_conditional_edges("orchestrator", assign_workers, ["slot_worker", "writer"])
    builder.add_edge("slot_worker", "batch_complete")
    _continue_map = {"orchestrator": "orchestrator", "writer": "writer", "verification": "verification"}
    builder.add_conditional_edges("batch_complete", should_continue, _continue_map)
    builder.add_conditional_edges("verification", should_continue, _continue_map)
    builder.add_edge("writer", END)
    return builder.compile(checkpointer=checkpointer, store=store)


research_graph = _build()

_research_graph_checkpointed: object | None = None


def get_or_build_research_graph(checkpointer=None, store=None) -> object:
    global _research_graph_checkpointed
    if checkpointer is None:
        return research_graph
    if _research_graph_checkpointed is None:
        _research_graph_checkpointed = _build(checkpointer=checkpointer, store=store)
    return _research_graph_checkpointed


# ── Public API ────────────────────────────────────────────────────────────────

def final_rs_from_result(result: ResearchGraphState) -> ResearchState:
    return ResearchState(
        question=result["question"],
        document_ids=result["document_ids"],
        document_context=result["document_context"],
        task_goal=result.get("task_goal", ""),
        coverage_items=result.get("coverage_items", []),
        output_contract=result.get("output_contract", ""),
        search_count=result["search_count"],
        consecutive_no_new=result["consecutive_no_new"],
        verification_done=result["verification_done"],
        known_keywords=result["known_keywords"],
        used_queries=result["used_queries"],
        slot_status=result["slot_status"],
        evidence=result["evidence"],
        evidence_details=result.get("evidence_details", {}),
        sources=result["sources"],
        last_reflection=result.get("last_reflection", ""),
        next_search_angle=result.get("next_search_angle", ""),
        suggested_query_terms=result.get("suggested_query_terms", []),
        avoid_query_terms=result.get("avoid_query_terms", []),
    )


def cbq_from_result(result: ResearchGraphState) -> list[tuple[SearchStep, list[dict]]]:
    out: list[tuple[SearchStep, list[dict]]] = []
    for entry in result.get("chunks_by_query_json", []):
        step_data = entry["step"]
        step = SearchStep(
            slot=step_data["slot"],
            query=step_data.get("query", step_data.get("keyword_query", "")),
            display_intent=step_data.get("display_intent", ""),
            keyword_query=step_data.get("keyword_query", ""),
            semantic_query=step_data.get("semantic_query", ""),
            section_terms=step_data.get("section_terms", []),
            use_hyde=bool(step_data.get("use_hyde", False)),
            quality=step_data.get("quality", "UNKNOWN"),
            new_keywords=step_data.get("new_keywords", []),
            note=step_data.get("note", ""),
            thought=step_data.get("thought", ""),
            expected_evidence=step_data.get("expected_evidence", ""),
            planner_rationale=step_data.get("planner_rationale", ""),
            missing_gap=step_data.get("missing_gap", ""),
            next_search_angle=step_data.get("next_search_angle", ""),
        )
        out.append((step, entry["chunks"]))
    return out
