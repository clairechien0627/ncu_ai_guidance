import asyncio
import os
import sys
import types
from dataclasses import dataclass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


from agents.research.scheduler import decide_slot_ordering, get_candidate_slots  # noqa: E402
from agents.research.planner import PlannerDecision  # noqa: E402
from agents.research.research_graph import (  # noqa: E402
    _force_next_angle_if_stalled,
    _merge_stream_patch,
    _next_coverage_slot,
    should_continue,
)
from agents.research.state import (  # noqa: E402
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


def test_next_coverage_slot_prefers_untried_required_slot():
    items = _items(("A", True, False), ("B", True, False))
    rs = _rs(items, {"A": "FILLED", "B": "NOT_FILLED"})

    assert _next_coverage_slot(_gs(items), rs) == "B"


def test_next_coverage_slot_skips_stalled_slot_temporarily():
    items = _items(("A", True, False), ("B", True, False))
    steps = [
        {"slot": "A", "quality": "NO_RESULTS"},
        {"slot": "A", "quality": "NOT_USEFUL"},
    ]
    rs = _rs(items, {"A": "PARTIAL", "B": "NOT_FILLED"})

    assert _next_coverage_slot(_gs(items, steps), rs) == "B"


def test_consecutive_gate_waits_until_required_slots_are_directly_searched():
    items = _items(("A", True, False), ("B", True, False))
    state = {
        "coverage_items": items,
        "steps_json": [{"slot": "A", "quality": "NO_RESULTS"}],
        "slot_status": {"A": "PARTIAL", "B": "PARTIAL"},
        "evidence": {},
        "evidence_details": {},
        "document_ids": [1],
        "document_context": "",
        "question": "q",
        "task_goal": "g",
        "output_contract": "",
        "known_keywords": [],
        "used_queries": [],
        "sources": [],
        "search_count": 1,
        "max_searches": 10,
        "max_searches_per_slot": 7,
        "max_consecutive_no_new": 1,
        "consecutive_no_new": 1,
        "verification_done": False,
    }

    assert should_continue(state) == "orchestrator"


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


def test_get_candidate_slots_filters_filled_exhausted_and_cap():
    items = _items(("A", True, False), ("B", True, False), ("C", True, False), ("D", True, False))
    rs = _rs(items, {"A": "FILLED", "B": "EXHAUSTED", "C": "PARTIAL", "D": "NOT_FILLED"})

    candidates = get_candidate_slots(
        {"coverage_items": items},
        rs,
        {"C": 2, "D": 1},
        per_slot_cap=2,
    )

    assert candidates == ["D"]


def test_assign_workers_returns_send_for_each_batch_item():
    state = {
        "batch_plan": [{"slot": "A", "hint": "ha"}, {"slot": "B", "hint": "hb"}],
        "question": "q",
    }

    sends = assign_workers(state)

    assert [send.node for send in sends] == ["slot_worker", "slot_worker"]
    assert [send.arg["worker_slot"] for send in sends] == ["A", "B"]
    assert [send.arg["worker_hint"] for send in sends] == ["ha", "hb"]


def test_reducers_merge_worker_deltas_without_overwriting_parallel_results():
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


class FailingLLM:
    async def ainvoke(self, _messages):
        raise RuntimeError("boom")


def test_orchestrator_fallback_assigns_first_two_candidates():
    items = _items(("A", True, False), ("B", True, False), ("C", True, False))
    rs = _rs(items)

    decision = asyncio.run(decide_next_batch(FailingLLM(), rs, ["A", "B", "C"]))

    assert [slot.slot_id for slot in decision.slots] == ["A", "B"]
    assert decision.rationale == "fallback"


@dataclass
class _Reflection:
    quality: str = "USEFUL"
    updated_slots: list[str] = None
    updates: list = None
    missing_gap: str = ""
    next_search_angle: str = ""
    new_keywords: list[str] = None
    rationale: str = "ok"
    suggested_query_terms: list[str] = None
    avoid_query_terms: list[str] = None

    def __post_init__(self):
        self.updated_slots = self.updated_slots or ["A"]
        self.updates = self.updates or [types.SimpleNamespace(item_id="A", notes=["evidence text"])]
        self.new_keywords = self.new_keywords or []
        self.suggested_query_terms = self.suggested_query_terms or []
        self.avoid_query_terms = self.avoid_query_terms or []


def test_slot_worker_returns_delta_patch(monkeypatch):
    from agents.research import research_graph as rg

    async def fake_plan_query_for_slot(_llm, _rs, slot, _hint=""):
        return PlannerDecision(
            thought="t",
            next_slot=slot,
            display_intent=slot,
            keyword_query="query",
            semantic_query="semantic",
            section_terms=["sec"],
            use_hyde=False,
            expected_evidence="e",
            rationale="r",
        )

    async def fake_retrieve_evidence(**_kwargs):
        return (
            [{"content": "evidence text", "filename": "doc.pdf", "page": 1, "score": 0.9}],
            ["doc.pdf"],
        )

    async def fake_reflect_results(_llm, state, slot, query, chunks):
        return _Reflection(next_search_angle="next")

    def fake_apply_reflection(rs, reflection):
        rs.slot_status["A"] = "PARTIAL"
        rs.evidence["A"] = ["evidence text"]
        rs.evidence_details["A"] = [{"content": "evidence text", "source": "doc.pdf"}]

    monkeypatch.setattr(rg, "plan_query_for_slot", fake_plan_query_for_slot)
    monkeypatch.setattr(rg, "retrieve_evidence", fake_retrieve_evidence)
    monkeypatch.setattr(rg, "reflect_results", fake_reflect_results)
    monkeypatch.setattr(rg, "apply_reflection", fake_apply_reflection)

    state: ResearchGraphState = {
        "messages": [],
        "question": "q",
        "document_ids": [1],
        "document_context": "",
        "task_goal": "g",
        "output_contract": "",
        "coverage_items": _items(("A", True, False)),
        "known_keywords": [],
        "search_count": 0,
        "max_searches": 5,
        "max_searches_per_slot": 2,
        "max_consecutive_no_new": 4,
        "consecutive_no_new": 0,
        "slot_status": {"A": "NOT_FILLED"},
        "evidence": {},
        "evidence_details": {},
        "sources": [],
        "verification_done": False,
        "used_queries": [],
        "seen_chunk_keys": [],
        "used_query_keys": [],
        "last_reflection": {},
        "next_search_angle": "",
        "suggested_query_terms": [],
        "avoid_query_terms": [],
        "final_answer": "",
        "final_sources": [],
        "batch_plan": [{"slot": "A", "hint": ""}],
        "_batch_evidence_flags": [],
        "steps_json": [],
        "chunks_by_query_json": [],
        "llm_call_count": 0,
        "trace_summary": {},
        "metadata": {},
        "worker_slot": "A",
        "worker_hint": "",
    }

    patch = asyncio.run(slot_worker_node(state, {"configurable": {"llm": object(), "on_stage": None}}))

    assert "question" not in patch
    assert patch["search_count"] == 1
    assert patch["llm_call_count"] == 2
    assert patch["slot_status"] == {"A": "PARTIAL"}
    assert patch["evidence"] == {"A": ["evidence text"]}
    assert patch["sources"] == ["doc.pdf"]
    assert patch["_batch_evidence_flags"] == [True]
