"""LangGraph node functions for the research graph."""

from __future__ import annotations

import logging

from langchain_core.runnables import RunnableConfig
from langfuse import observe
from langgraph.runtime import Runtime

from .graph_utils import (
    _cap_list,
    _chunk_key,
    _compact_chunks,
    _compact_evidence,
    _compact_evidence_details,
    _coverage_status_for_trace,
    _evidence_counts,
    _force_decision_slot,
    _force_next_angle_if_stalled,
    _force_unique_decision,
    _graph_to_rs,
    _hard_max_searches,
    _MAX_STATE_KEYWORDS,
    _normalize_query,
    _per_slot_cap,
    _reserve_query,
    _safe_update_current_observation,
    _seed_keywords,
    _slot_label,
    _slot_search_counts,
    _tool_result_content,
    _trace_summary,
    _verification_display_intent,
    _verification_section_terms,
    _verification_query,
)
from .orchestrator import decide_next_batch, get_candidate_slots
from .planner import build_slot_decision, plan_query_for_slot, should_use_hyde
from .reflector import apply_reflection, reflect_results
from .retriever import retrieve_evidence
from .state import ResearchGraphState, WorkerState, _FLAGS_CLEAR
from .writer import ResearchWriteup, _fallback_writeup, write_summary

logger = logging.getLogger(__name__)


@observe(as_type="chain", name="orchestrator", capture_input=False, capture_output=False)
async def orchestrator_node(state: ResearchGraphState, config: RunnableConfig) -> dict:
    llm = config["configurable"]["llm"]
    on_stage = config["configurable"].get("on_stage")
    rs = _graph_to_rs(state)
    counts = _slot_search_counts(state)
    candidates = get_candidate_slots(state, rs, counts, _per_slot_cap(state))
    _safe_update_current_observation(
        input={
            "coverage_status": _coverage_status_for_trace(state),
            "candidate_slots": candidates,
            "slot_search_counts": counts,
            "search_count": state.get("search_count", 0),
        }
    )
    if not candidates:
        _safe_update_current_observation(output={"batch_plan": [], "rationale": "no candidates"})
        return {"batch_plan": []}
    decision = await decide_next_batch(llm, rs, candidates)
    batch_plan = [
        {"slot": item.slot_id, "hint": item.hint}
        for item in decision.slots[:3]
        if item.slot_id in candidates
    ]
    if on_stage and batch_plan:
        try:
            labels = ", ".join(_slot_label(state, item["slot"]) for item in batch_plan)
            on_stage(f"批次派工：{labels}")
        except Exception:
            pass
    _safe_update_current_observation(output={"batch_plan": batch_plan, "rationale": decision.rationale})
    return {"batch_plan": batch_plan, "llm_call_count": 1}


@observe(as_type="chain", name="slot_worker", capture_input=False, capture_output=False)
async def slot_worker_node(state: WorkerState, config: RunnableConfig, runtime: Runtime) -> dict:
    llm = config["configurable"]["llm"]
    on_stage = config["configurable"].get("on_stage")
    rs = _graph_to_rs(state)
    slot = state["worker_slot"]
    hint = state.get("worker_hint", "")
    round_index = int(state.get("search_count", 0)) + 1
    is_retry = runtime.execution_info.node_attempt > 1
    _safe_update_current_observation(
        input={
            "round": round_index,
            "slot": slot,
            "hint": hint,
            "attempt": runtime.execution_info.node_attempt,
            "coverage_status": _coverage_status_for_trace(state),
            "evidence_counts": _evidence_counts(state),
        }
    )

    # Drain: skip expensive work and return a no-op result so the superstep
    # completes cleanly and the checkpoint can be saved before shutdown.
    if runtime.drain_requested:
        logger.info("slot_worker skipping slot=%s (drain requested: %s)", slot, runtime.drain_reason)
        return {"search_count": 0, "_batch_evidence_flags": [False]}

    # On retry attempts skip LLM planning to avoid repeating the same failure.
    if is_retry:
        decision = build_slot_decision(rs, slot)
    else:
        try:
            decision = await plan_query_for_slot(llm, rs, slot, hint)
        except Exception as exc:
            logger.warning("slot_worker planner failed for %s: %s", slot, exc)
            decision = build_slot_decision(rs, slot)
    decision = _force_decision_slot(decision, rs, slot)
    if hint:
        decision = decision.model_copy(update={"keyword_query": _normalize_query(hint)})
    decision = _force_next_angle_if_stalled(decision, state)
    decision = _force_unique_decision(decision, rs)
    used_keys: set[str] = set(state.get("used_query_keys", []))
    final_query = _reserve_query(decision.keyword_query, used_keys, slot, state.get("search_count", 0))
    decision = decision.model_copy(update={"keyword_query": final_query})
    tool_call_id = f"research_search_{slot}_{state.get('search_count', 0) + 1}"

    if on_stage:
        try:
            on_stage(f"搜尋 {_slot_label(state, slot)}：{decision.display_intent}")
        except Exception:
            pass

    seen: set[str] = set(state.get("seen_chunk_keys", []))
    chunks, sources = await retrieve_evidence(
        query=final_query,
        display_intent=decision.display_intent,
        keyword_query=final_query,
        semantic_query=decision.semantic_query,
        section_terms=decision.section_terms,
        use_hyde=bool(decision.use_hyde),
        document_ids=state["document_ids"],
        seen_chunks=seen,
        on_stage=None,
        search_count=state.get("search_count", 0),
        consecutive_empty=state.get("consecutive_no_new", 0),
        max_searches=_hard_max_searches(state),
        max_consecutive_empty=state["max_consecutive_no_new"],
        task_type=str(state.get("metadata", {}).get("task_type") or "research"),
        route_intent=str(state.get("metadata", {}).get("route_intent") or "research"),
    )
    seed_keywords = _seed_keywords("\n".join(str(chunk.get("content", ""))[:1200] for chunk in chunks))

    status_before = dict(rs.slot_status)
    evidence_counts_before = {k: len(v) for k, v in rs.evidence.items()}
    details_counts_before = {k: len(v) for k, v in rs.evidence_details.items()}
    known_before = set(rs.known_keywords)
    reflection = await reflect_results(llm, state=rs, slot=slot, query=final_query, chunks=chunks)
    apply_reflection(rs, reflection)
    evidence_progress = bool(chunks) and (
        rs.slot_status != status_before
        or any(len(rs.evidence.get(k, [])) > evidence_counts_before.get(k, 0) for k in rs.evidence)
    )

    prior_same_no_new = 0
    if not evidence_progress:
        for previous in reversed(state.get("steps_json", [])):
            if previous.get("slot") != slot:
                break
            if previous.get("quality") in ("NO_RESULTS", "NOT_USEFUL"):
                prior_same_no_new += 1
            else:
                break
    same_slot_no_new = 0 if evidence_progress else prior_same_no_new + 1

    slot_status = dict(rs.slot_status)
    if same_slot_no_new >= state["max_consecutive_no_new"]:
        slot_status[slot] = "EXHAUSTED"
    current_slot_count = _slot_search_counts(state).get(slot, 0) + 1
    if current_slot_count >= _per_slot_cap(state) and slot_status.get(slot) != "FILLED":
        slot_status[slot] = "EXHAUSTED"

    updated_slots = [update.item_id for update in reflection.updates if update.notes]
    step_dict = {
        "slot": slot,
        "query": final_query,
        "display_intent": decision.display_intent,
        "keyword_query": final_query,
        "semantic_query": decision.semantic_query,
        "section_terms": decision.section_terms,
        "use_hyde": bool(decision.use_hyde),
        "thought": decision.thought,
        "expected_evidence": decision.expected_evidence,
        "planner_rationale": decision.rationale,
        "is_verification": False,
        "quality": reflection.quality,
        "new_keywords": reflection.new_keywords,
        "note": reflection.rationale,
        "missing_gap": reflection.missing_gap,
        "next_search_angle": reflection.next_search_angle,
        "chunk_count": len(chunks),
        "updated_slots": updated_slots,
    }
    cbq_entry = {"step": step_dict, "chunks": _compact_chunks(chunks)}
    evidence_delta = {
        item_id: rs.evidence.get(item_id, [])[evidence_counts_before.get(item_id, 0):]
        for item_id in rs.evidence
        if len(rs.evidence.get(item_id, [])) > evidence_counts_before.get(item_id, 0)
    }
    details_delta = {
        item_id: rs.evidence_details.get(item_id, [])[details_counts_before.get(item_id, 0):]
        for item_id in rs.evidence_details
        if len(rs.evidence_details.get(item_id, [])) > details_counts_before.get(item_id, 0)
    }
    status_delta = {item_id: slot_status.get(item_id, "NOT_FILLED") for item_id in {slot, *updated_slots}}
    new_keywords = [
        keyword
        for keyword in [*seed_keywords, *rs.known_keywords]
        if keyword and keyword not in known_before
    ]
    _safe_update_current_observation(
        output={
            "round": round_index,
            "slot": slot,
            "query": final_query,
            "chunk_count": len(chunks),
            "quality": reflection.quality,
            "updated_slots": updated_slots,
            "missing_gap": reflection.missing_gap,
            "next_search_angle": reflection.next_search_angle,
            "slot_status": status_delta,
            "found_new_evidence": evidence_progress,
            "sources": sources[:5],
        }
    )
    return {
        "slot_status": status_delta,
        "evidence": _compact_evidence(evidence_delta, limit=12),
        "evidence_details": _compact_evidence_details(details_delta, limit=12),
        "sources": sources,
        "known_keywords": _cap_list(new_keywords, _MAX_STATE_KEYWORDS),
        "used_queries": [final_query],
        "used_query_keys": [final_query],
        "seen_chunk_keys": [_chunk_key(chunk) for chunk in chunks],
        "last_reflection": reflection.rationale,
        "next_search_angle": reflection.next_search_angle,
        "suggested_query_terms": [term.strip() for term in reflection.suggested_query_terms if term.strip()],
        "avoid_query_terms": [term.strip() for term in reflection.avoid_query_terms if term.strip()],
        "search_count": 1,
        "llm_call_count": 2,
        "steps_json": [step_dict],
        "chunks_by_query_json": [cbq_entry],
        "_batch_evidence_flags": [bool(evidence_progress or slot_status.get(slot) == "FILLED")],
        "messages": [
            {
                "type": "ai",
                "content": decision.thought or decision.rationale,
                "tool_calls": [{
                    "name": "search_report",
                    "id": tool_call_id,
                    "args": {
                        "keyword_query": final_query,
                        "semantic_query": decision.semantic_query,
                        "section_terms": decision.section_terms,
                        "use_hyde": decision.use_hyde,
                        "slot": slot,
                        "display_intent": decision.display_intent,
                        "expected_evidence": decision.expected_evidence,
                    },
                }],
            },
            {
                "type": "tool",
                "name": "search_report",
                "tool_call_id": tool_call_id,
                "content": _tool_result_content(chunks),
            },
            {
                "type": "ai",
                "content": f"slot={slot} quality={reflection.quality}; gap={reflection.missing_gap}; next={reflection.next_search_angle}",
            },
        ],
    }


def batch_complete_node(state: ResearchGraphState) -> dict:
    flags = state.get("_batch_evidence_flags", [])
    found = any(flags)
    return {
        "consecutive_no_new": 0 if found else state["consecutive_no_new"] + 1,
        "batch_plan": [],
        "_batch_evidence_flags": _FLAGS_CLEAR,
    }


@observe(as_type="chain", name="verification", capture_input=False, capture_output=False)
async def verification_node(state: ResearchGraphState, config: RunnableConfig) -> dict:
    llm = config["configurable"]["llm"]
    on_stage = config["configurable"].get("on_stage")
    rs = _graph_to_rs(state)
    slot = rs.weakest_slot()
    round_index = int(state.get("search_count", 0)) + 1
    _safe_update_current_observation(
        input={
            "round": round_index,
            "slot": slot,
            "coverage_status": _coverage_status_for_trace(state),
            "evidence_counts": _evidence_counts(state),
        }
    )

    decision = build_slot_decision(rs, slot)
    decision = decision.model_copy(update={
        "keyword_query": _verification_query(rs, slot) or decision.keyword_query,
        "display_intent": _verification_display_intent(rs, slot),
        "section_terms": _verification_section_terms(rs, slot),
        "thought": f"Verification pass for {rs.coverage_label(slot)}.",
        "rationale": f"Verify weakest slot {rs.coverage_label(slot)}.",
        "use_hyde": should_use_hyde(rs, slot, requested=False),
    })
    decision = _force_unique_decision(decision, rs)
    used_keys: set[str] = set(state.get("used_query_keys", []))
    final_query = _reserve_query(decision.keyword_query, used_keys, slot, state.get("search_count", 0))
    decision = decision.model_copy(update={"keyword_query": final_query})
    tool_call_id = f"research_verify_{slot}_{state.get('search_count', 0) + 1}"

    if on_stage:
        try:
            on_stage(f"驗證 {_slot_label(state, slot)}：{decision.display_intent}")
        except Exception:
            pass

    seen: set[str] = set(state.get("seen_chunk_keys", []))
    chunks, sources = await retrieve_evidence(
        query=final_query,
        display_intent=decision.display_intent,
        keyword_query=final_query,
        semantic_query=decision.semantic_query,
        section_terms=decision.section_terms,
        use_hyde=bool(decision.use_hyde),
        document_ids=state["document_ids"],
        seen_chunks=seen,
        on_stage=None,
        search_count=state.get("search_count", 0),
        consecutive_empty=state.get("consecutive_no_new", 0),
        max_searches=_hard_max_searches(state),
        max_consecutive_empty=state["max_consecutive_no_new"],
        task_type=str(state.get("metadata", {}).get("task_type") or "research"),
        route_intent=str(state.get("metadata", {}).get("route_intent") or "research"),
    )
    seed_keywords = _seed_keywords("\n".join(str(chunk.get("content", ""))[:1200] for chunk in chunks))

    evidence_counts_before = {k: len(v) for k, v in rs.evidence.items()}
    details_counts_before = {k: len(v) for k, v in rs.evidence_details.items()}
    known_before = set(rs.known_keywords)
    reflection = await reflect_results(llm, state=rs, slot=slot, query=final_query, chunks=chunks)
    apply_reflection(rs, reflection)

    updated_slots = [update.item_id for update in reflection.updates if update.notes]
    step_dict = {
        "slot": slot,
        "query": final_query,
        "display_intent": decision.display_intent,
        "keyword_query": final_query,
        "semantic_query": decision.semantic_query,
        "section_terms": decision.section_terms,
        "use_hyde": bool(decision.use_hyde),
        "thought": decision.thought,
        "expected_evidence": decision.expected_evidence,
        "planner_rationale": decision.rationale,
        "is_verification": True,
        "quality": reflection.quality,
        "new_keywords": reflection.new_keywords,
        "note": reflection.rationale,
        "missing_gap": reflection.missing_gap,
        "next_search_angle": reflection.next_search_angle,
        "chunk_count": len(chunks),
        "updated_slots": updated_slots,
    }
    cbq_entry = {"step": step_dict, "chunks": _compact_chunks(chunks)}
    evidence_delta = {
        item_id: rs.evidence.get(item_id, [])[evidence_counts_before.get(item_id, 0):]
        for item_id in rs.evidence
        if len(rs.evidence.get(item_id, [])) > evidence_counts_before.get(item_id, 0)
    }
    details_delta = {
        item_id: rs.evidence_details.get(item_id, [])[details_counts_before.get(item_id, 0):]
        for item_id in rs.evidence_details
        if len(rs.evidence_details.get(item_id, [])) > details_counts_before.get(item_id, 0)
    }
    slot_status = dict(rs.slot_status)
    status_delta = {item_id: slot_status.get(item_id, "NOT_FILLED") for item_id in {slot, *updated_slots}}
    new_keywords = [
        keyword
        for keyword in [*seed_keywords, *rs.known_keywords]
        if keyword and keyword not in known_before
    ]
    _safe_update_current_observation(
        output={
            "round": round_index,
            "slot": slot,
            "query": final_query,
            "chunk_count": len(chunks),
            "quality": reflection.quality,
            "updated_slots": updated_slots,
        }
    )
    return {
        "slot_status": status_delta,
        "evidence": _compact_evidence(evidence_delta, limit=12),
        "evidence_details": _compact_evidence_details(details_delta, limit=12),
        "sources": sources,
        "known_keywords": _cap_list(new_keywords, _MAX_STATE_KEYWORDS),
        "used_queries": [final_query],
        "used_query_keys": [final_query],
        "seen_chunk_keys": [_chunk_key(chunk) for chunk in chunks],
        "last_reflection": reflection.rationale,
        "next_search_angle": reflection.next_search_angle,
        "suggested_query_terms": [term.strip() for term in reflection.suggested_query_terms if term.strip()],
        "avoid_query_terms": [term.strip() for term in reflection.avoid_query_terms if term.strip()],
        "search_count": 1,
        "llm_call_count": 1,
        "steps_json": [step_dict],
        "chunks_by_query_json": [cbq_entry],
        "verification_done": True,
        "messages": [
            {
                "type": "ai",
                "content": decision.thought or decision.rationale,
                "tool_calls": [{
                    "name": "search_report",
                    "id": tool_call_id,
                    "args": {
                        "keyword_query": final_query,
                        "section_terms": decision.section_terms,
                        "slot": slot,
                        "display_intent": decision.display_intent,
                    },
                }],
            },
            {
                "type": "tool",
                "name": "search_report",
                "tool_call_id": tool_call_id,
                "content": _tool_result_content(chunks),
            },
            {
                "type": "ai",
                "content": f"verification slot={slot} quality={reflection.quality}; done=True",
            },
        ],
    }


@observe(as_type="chain", name="writer", capture_input=False, capture_output=False)
async def writer_node(state: ResearchGraphState, config: RunnableConfig) -> dict:
    llm = config["configurable"]["llm"]
    on_stage = config["configurable"].get("on_stage")
    if on_stage:
        try:
            on_stage("產生研究整理")
        except Exception:
            pass
    rs = _graph_to_rs(state)
    required_labels = {rs.coverage_label(slot) for slot in rs.required_coverage_ids() if rs.coverage_label(slot)}
    _safe_update_current_observation(
        input={
            "coverage_status": _coverage_status_for_trace(state),
            "evidence_counts": _evidence_counts(state),
            "source_count": len(state.get("sources", [])),
            "search_count": state.get("search_count", 0),
        }
    )

    def _answer_ok(w: ResearchWriteup) -> bool:
        return bool(
            w.answer
            and len(w.answer.strip()) >= 150
            and all(label in w.answer for label in required_labels)
        )

    writeup: ResearchWriteup = await write_summary(llm, rs)
    llm_calls = 1

    if not _answer_ok(writeup):
        missing = [label for label in required_labels if label not in (writeup.answer or "")]
        feedback = f"缺少以下必要段落：{', '.join(missing)}" if missing else "答案長度不足（需至少 150 字）"
        logger.warning("writer_node: quality gate failed (%d chars), retrying with feedback: %s", len(writeup.answer or ""), feedback)
        writeup = await write_summary(llm, rs, feedback=feedback)
        llm_calls = 2
        if not _answer_ok(writeup):
            logger.warning("writer_node: retry also failed, using evidence fallback")
            writeup = _fallback_writeup(rs)

    final_sources = writeup.sources if writeup.sources else rs.sources[:5]
    final_state = dict(state)
    final_state.update({"final_answer": writeup.answer, "final_sources": final_sources})
    _safe_update_current_observation(
        output={
            "answer_preview": writeup.answer[:600],
            "answer_length": len(writeup.answer),
            "sources": final_sources[:8],
            "source_count": len(final_sources),
            "llm_calls": llm_calls,
        }
    )
    return {
        "final_answer": writeup.answer,
        "final_sources": final_sources,
        "messages": [{"type": "ai", "content": writeup.answer, "sources": final_sources}],
        "trace_summary": _trace_summary(final_state),
        "llm_call_count": llm_calls,
    }
