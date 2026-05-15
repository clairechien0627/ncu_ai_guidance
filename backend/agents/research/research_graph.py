"""LangGraph research graph — routing, error handlers, graph construction, public API."""

from __future__ import annotations

import logging
from typing import Literal

from langgraph.errors import NodeError
from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy, TimeoutPolicy

from .graph_nodes import (
    scheduler_node,
    slot_executor_node,
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
from .state import DEFAULT_SUMMARY_COVERAGE, ResearchGraphState, ResearchState, SearchStep
from .writer import _fallback_writeup

logger = logging.getLogger(__name__)

# Re-export for agent.py imports
__all__ = [
    "_hard_max_searches",
    "_merge_stream_patch",
    "_seed_keywords",
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


def should_continue(state: ResearchGraphState) -> Literal["scheduler", "writer"]:
    """Route after each executor round. Reads state directly."""
    if state["search_count"] >= _hard_max_searches(state):
        return "writer"

    required = _required_ids_from_state(state)
    slot_status: dict = state.get("slot_status", {})
    counts = _slot_search_counts(state)
    per_slot = _per_slot_cap(state)
    min_ev = state.get("min_evidence_per_slot", 0)

    # Terminal statuses that mean "done searching this slot"
    terminal = {"FILLED", "EXHAUSTED", "NOT_FOUND", "OMITTED"}

    # All required slots settled
    all_settled = all(slot_status.get(s, "NOT_FILLED") in terminal for s in required)
    if all_settled:
        if min_ev <= 0:
            return "writer"
        sufficient = all(
            slot_status.get(s) in {"EXHAUSTED", "NOT_FOUND", "OMITTED"}
            or len(state.get("evidence", {}).get(s, [])) >= min_ev
            for s in required
        )
        if sufficient:
            return "writer"

    # Stalled: consecutive-no-new limit hit + all required slots searched at least once
    if state["consecutive_no_new"] >= state.get("max_consecutive_no_new", 4):
        if all(counts.get(s, 0) > 0 for s in required):
            return "writer"

    # No more searchable candidates
    candidates = [
        s for s in required
        if slot_status.get(s) not in terminal and counts.get(s, 0) < per_slot
    ]
    if not candidates:
        return "writer"

    return "scheduler"


# ── Fault-tolerance error handlers ───────────────────────────────────────────

def _scheduler_error_handler(state: ResearchGraphState, error: NodeError) -> dict:
    logger.warning("scheduler exhausted retries (%s); clearing pending_slots", error.error)
    return {"pending_slots": []}


def _slot_executor_error_handler(state: ResearchGraphState, error: NodeError) -> dict:
    logger.warning("slot_executor exhausted retries (%s); using fallback state", error.error)
    return {
        "pending_slots": [],
        "search_count": 1,
        "consecutive_no_new": state.get("consecutive_no_new", 0) + 1,
    }


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
        "scheduler", scheduler_node,
        retry_policy=RetryPolicy(max_attempts=2, initial_interval=1.0),
        timeout=TimeoutPolicy(run_timeout=90),
        error_handler=_scheduler_error_handler,
    )
    builder.add_node(
        "slot_executor", slot_executor_node,
        retry_policy=RetryPolicy(max_attempts=2, initial_interval=2.0),
        timeout=TimeoutPolicy(run_timeout=600),  # executor runs multiple slots
        error_handler=_slot_executor_error_handler,
    )
    builder.add_node(
        "writer", writer_node,
        retry_policy=RetryPolicy(max_attempts=2, initial_interval=2.0),
        timeout=TimeoutPolicy(run_timeout=240),
        error_handler=_writer_error_handler,
    )
    builder.add_edge(START, "scheduler")
    builder.add_edge("scheduler", "slot_executor")
    builder.add_conditional_edges("slot_executor", should_continue, {"scheduler": "scheduler", "writer": "writer"})
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
        verification_done=False,
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
