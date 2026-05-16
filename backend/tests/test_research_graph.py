import asyncio
import os
import sys
import types
from dataclasses import dataclass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.research.scheduler import decide_slot_ordering, get_candidate_slots
from agents.research.planner import PlannerDecision
from agents.research.research_graph import (
    _force_next_angle_if_stalled,
    _merge_stream_patch,
    should_continue,
)
from agents.research.state import (
    ResearchGraphState,
    ResearchState,
    _merge_evidence_dict,
    _merge_unique_list,
)


def _items(*specs):
    return [
        {"id": sid, "label": sid, "description": sid, "required": req, "use_hyde": hyde}
        for sid, req, hyde in specs
    ]


def _gs(items, steps=None, cap=7):
    return {
        "coverage_items": items,
        "steps_json": steps or [],
        "max_searches": 0,
        "max_searches_per_slot": cap,
    }


def _rs(items, slot_status: dict | None = None):
    rs = ResearchState(question="q", document_ids=[1], coverage_items=items)
    if slot_status:
        rs.slot_status.update(slot_status)
    return rs


def _base_state(items, slot_status=None, steps=None, search_count=0, consecutive_no_new=0):
    status = slot_status or {item["id"]: "NOT_FILLED" for item in items}
    return {
        "coverage_items": items,
        "steps_json": steps or [],
        "slot_status": status,
        "evidence": {item["id"]: [] for item in items},
        "evidence_details": {},
        "document_ids": [1],
        "document_context": "",
        "question": "q",
        "task_goal": "g",
        "output_contract": "",
        "known_keywords": [],
        "used_queries": [],
        "sources": [],
        "search_count": search_count,
        "max_searches": 10,
        "max_searches_per_slot": 7,
        "max_consecutive_no_new": 4,
        "consecutive_no_new": consecutive_no_new,
        "min_evidence_per_slot": 0,
        "scheduled_slot": None,
        "void_slot_attempts": {},
    }


# ── get_candidate_slots ────────────────────────────────────────────────────────

def test_get_candidate_slots_filters_terminal_and_cap():
    items = _items(("A", True, False), ("B", True, False), ("C", True, False), ("D", True, False))
    rs = _rs(items, {"A": "FILLED", "B": "EXHAUSTED", "C": "PARTIAL", "D": "NOT_FILLED"})

    candidates = get_candidate_slots(
        {"coverage_items": items},
        rs,
        {"C": 2, "D": 1},
        per_slot_cap=2,
    )

    assert candidates == ["D"]


def test_get_candidate_slots_returns_partial_under_cap():
    items = _items(("A", True, False), ("B", True, False))
    rs = _rs(items, {"A": "PARTIAL", "B": "NOT_FILLED"})

    candidates = get_candidate_slots({"coverage_items": items}, rs, {"A": 1}, per_slot_cap=3)

    assert set(candidates) == {"A", "B"}


# ── should_continue ────────────────────────────────────────────────────────────

def test_should_continue_routes_to_writer_when_all_terminal():
    items = _items(("A", True, False), ("B", True, False))
    state = _base_state(items, {"A": "FILLED", "B": "EXHAUSTED"})

    assert should_continue(state) == "writer"


def test_should_continue_routes_to_scheduler_when_partial_slots_remain():
    items = _items(("A", True, False), ("B", True, False))
    state = _base_state(items, {"A": "FILLED", "B": "PARTIAL"})

    assert should_continue(state) == "scheduler"


def test_should_continue_routes_to_writer_when_budget_exhausted():
    items = _items(("A", True, False),)
    state = _base_state(items, {"A": "PARTIAL"}, search_count=100)

    assert should_continue(state) == "writer"


def test_should_continue_routes_to_writer_when_stalled_and_all_searched():
    items = _items(("A", True, False), ("B", True, False))
    steps = [{"slot": "A"}, {"slot": "B"}]
    state = _base_state(items, {"A": "PARTIAL", "B": "PARTIAL"}, steps=steps, consecutive_no_new=4)

    assert should_continue(state) == "writer"


def test_should_continue_stays_if_not_all_searched_when_stalled():
    items = _items(("A", True, False), ("B", True, False))
    steps = [{"slot": "A"}]
    state = _base_state(items, {"A": "PARTIAL", "B": "NOT_FILLED"}, steps=steps, consecutive_no_new=4)

    assert should_continue(state) == "scheduler"


def test_should_continue_not_found_omitted_are_terminal():
    items = _items(("A", True, False), ("B", False, False))
    state = _base_state(items, {"A": "NOT_FOUND", "B": "OMITTED"})

    assert should_continue(state) == "writer"


# ── force_next_angle ───────────────────────────────────────────────────────────

def test_force_next_angle_overrides_keyword_after_failed_attempt():
    decision = PlannerDecision(
        thought="t",
        next_slot="A",
        display_intent="A",
        keyword_query="old query",
        semantic_query="semantic",
        section_terms=[],
        use_hyde=False,
        expected_evidence="e",
        rationale="r",
    )
    state = {
        "steps_json": [
            {"slot": "A", "quality": "NOT_USEFUL", "next_search_angle": "new angle"}
        ]
    }

    forced = _force_next_angle_if_stalled(decision, state)

    assert forced.keyword_query == "new angle"


# ── reducers ───────────────────────────────────────────────────────────────────

def test_reducers_merge_deltas():
    assert _merge_unique_list(["a", "b"], ["b", "c"]) == ["a", "b", "c"]
    assert _merge_evidence_dict({"A": ["x"]}, {"A": ["x", "y"], "B": ["z"]}) == {
        "A": ["x", "y"],
        "B": ["z"],
    }

    merged = _merge_stream_patch(
        {"sources": ["s1"], "steps_json": [{"slot": "A"}], "search_count": 1, "llm_call_count": 2},
        {"sources": ["s1", "s2"], "steps_json": [{"slot": "B"}], "search_count": 1, "llm_call_count": 2},
    )

    assert merged["sources"] == ["s1", "s2"]
    assert [step["slot"] for step in merged["steps_json"]] == ["A", "B"]
    assert merged["search_count"] == 2
    assert merged["llm_call_count"] == 4


# ── scheduler fallback ─────────────────────────────────────────────────────────

class FailingLLM:
    async def ainvoke(self, _messages):
        raise RuntimeError("boom")


def test_scheduler_fallback_returns_all_candidates_in_order():
    items = _items(("A", True, False), ("B", True, False), ("C", True, False))
    rs = _rs(items)

    decision = asyncio.run(decide_slot_ordering(FailingLLM(), rs, ["A", "B", "C"], {}))

    assert [slot.slot_id for slot in decision.slots] == ["A", "B", "C"]
    assert decision.rationale == "fallback"
