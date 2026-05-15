"""LangGraph node functions for the research graph."""

from __future__ import annotations

import asyncio
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
    _verification_section_terms,
    _verification_query,
)
from .scheduler import decide_slot_ordering, get_candidate_slots
from .planner import build_slot_decision, plan_query_for_slot
from .reflector import apply_reflection, reflect_results
from .retriever import retrieve_evidence
from .state import ResearchGraphState
from .writer import ResearchWriteup, _fallback_writeup, write_summary

logger = logging.getLogger(__name__)

# Maximum void attempts before a slot is declared NOT_FOUND / OMITTED.
MAX_VOID_TOTAL = 4


# ── scheduler_node ────────────────────────────────────────────────────────────

@observe(as_type="chain", name="scheduler", capture_input=False, capture_output=False)
async def scheduler_node(state: ResearchGraphState, config: RunnableConfig) -> dict:
    """Decide which slots to run and in what order; emit pending_slots for executor."""
    llm = config["configurable"]["llm"]
    on_stage = config["configurable"].get("on_stage")
    rs = _graph_to_rs(state)
    counts = _slot_search_counts(state)
    void_slot_attempts: dict[str, int] = state.get("void_slot_attempts") or {}
    candidates = get_candidate_slots(state, rs, counts, _per_slot_cap(state))

    _safe_update_current_observation(
        input={
            "coverage_status": _coverage_status_for_trace(state),
            "candidates": candidates,
            "void_slot_attempts": void_slot_attempts,
            "search_count": state.get("search_count", 0),
        }
    )

    if not candidates:
        _safe_update_current_observation(output={"pending_slots": [], "rationale": "no candidates"})
        return {"pending_slots": []}

    decision = await decide_slot_ordering(llm, rs, candidates, void_slot_attempts)
    pending_slots = [
        {"slot": item.slot_id, "hint": item.hint}
        for item in decision.slots
        if item.slot_id in set(candidates)
    ]

    if on_stage and pending_slots:
        try:
            labels = ", ".join(
                _slot_label(state, item["slot"]) for item in pending_slots
            )
            on_stage(f"排程搜尋：{labels}")
        except Exception:
            pass

    _safe_update_current_observation(
        output={"pending_slots": pending_slots, "rationale": decision.rationale}
    )
    return {
        "pending_slots": pending_slots,
        "void_slot_attempts": {},  # clear — executor will update
        "llm_call_count": 1,
    }


# ── _run_single_slot ──────────────────────────────────────────────────────────

async def _run_single_slot(
    slot: str,
    hint: str,
    state: ResearchGraphState,
    config: RunnableConfig,
    use_verification_query: bool = False,
) -> dict:
    """
    Core slot logic: plan → retrieve → reflect.
    Returns a result dict including `_last_quality` for the executor.
    NOT a LangGraph node — called by slot_executor_node.
    """
    llm = config["configurable"]["llm"]
    on_stage = config["configurable"].get("on_stage")
    rs = _graph_to_rs(state)
    round_index = int(state.get("search_count", 0)) + 1

    if on_stage:
        try:
            on_stage(f"規劃查詢策略：{_slot_label(state, slot)}")
        except Exception:
            pass

    if use_verification_query:
        decision = build_slot_decision(rs, slot)
        decision = decision.model_copy(update={
            "keyword_query": _verification_query(rs, slot) or decision.keyword_query,
            "display_intent": f"最後嘗試：{rs.coverage_label(slot)}",
            "section_terms": _verification_section_terms(rs, slot),
        })
    else:
        try:
            decision = await plan_query_for_slot(llm, rs, slot, hint)
        except Exception as exc:
            logger.warning("_run_single_slot planner failed for %s: %s", slot, exc)
            decision = build_slot_decision(rs, slot)

    decision = _force_decision_slot(decision, rs, slot)
    if hint and not use_verification_query:
        decision = decision.model_copy(update={"keyword_query": _normalize_query(hint)})
    decision = _force_next_angle_if_stalled(decision, state)
    decision = _force_unique_decision(decision, rs)
    used_keys: set[str] = set(state.get("used_query_keys", []))
    final_query = _reserve_query(decision.keyword_query, used_keys, slot, state.get("search_count", 0))
    decision = decision.model_copy(update={"keyword_query": final_query})
    tool_call_id = f"research_search_{slot}_{round_index}"

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
        "is_verification": use_verification_query,
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
        # Executor reads this to decide void handling:
        "_last_quality": reflection.quality,
    }


async def _run_single_slot_with_retry(
    slot: str,
    hint: str,
    state: ResearchGraphState,
    config: RunnableConfig,
    use_verification_query: bool = False,
    max_retries: int = 2,
    timeout_seconds: float = 180,
) -> dict:
    """Wrap _run_single_slot with asyncio-level retry and timeout."""
    for attempt in range(max_retries):
        try:
            return await asyncio.wait_for(
                _run_single_slot(slot, hint, state, config, use_verification_query),
                timeout=timeout_seconds,
            )
        except Exception as exc:
            if attempt == max_retries - 1:
                logger.warning(
                    "_run_single_slot failed for slot=%s after %d attempts: %s",
                    slot, max_retries, exc,
                )
                return {
                    "slot_status": {slot: "EXHAUSTED"},
                    "search_count": 1,
                    "steps_json": [{
                        "slot": slot,
                        "query": slot,
                        "display_intent": f"錯誤：{type(exc).__name__}",
                        "quality": "ERROR",
                        "chunk_count": 0,
                        "updated_slots": [],
                        "missing_gap": str(exc)[:120],
                        "next_search_angle": "",
                        "is_verification": use_verification_query,
                    }],
                    "_last_quality": "ERROR",
                }
            logger.debug("_run_single_slot attempt %d failed for %s: %s", attempt + 1, slot, exc)
    return {}  # unreachable


# ── slot_executor_node ────────────────────────────────────────────────────────

@observe(as_type="chain", name="slot_executor", capture_input=False, capture_output=False)
async def slot_executor_node(
    state: ResearchGraphState,
    config: RunnableConfig,
    runtime: Runtime,
) -> dict:
    """
    Reactive executor: runs pending_slots with asyncio.Semaphore(3).
    Each slot reads the latest accumulated state (reactive context).
    Void slots: up to MAX_VOID_TOTAL times before NOT_FOUND/OMITTED.
    """
    pending = list(state.get("pending_slots") or [])
    if not pending:
        return {"pending_slots": []}

    if runtime.drain_requested:
        logger.info("slot_executor skipping (drain requested: %s)", runtime.drain_reason)
        return {"pending_slots": [], "search_count": 0}

    on_stage = config["configurable"].get("on_stage")

    semaphore = asyncio.Semaphore(3)
    lock = asyncio.Lock()

    # Shared accumulated state — starts from current, updated as slots complete
    accumulated: dict = {
        "slot_status": dict(state.get("slot_status") or {}),
        "evidence": {k: list(v) for k, v in (state.get("evidence") or {}).items()},
        "evidence_details": {k: list(v) for k, v in (state.get("evidence_details") or {}).items()},
        "sources": list(state.get("sources") or []),
        "known_keywords": list(state.get("known_keywords") or []),
        "used_queries": list(state.get("used_queries") or []),
        "used_query_keys": list(state.get("used_query_keys") or []),
        "seen_chunk_keys": list(state.get("seen_chunk_keys") or []),
        "last_reflection": state.get("last_reflection", ""),
        "next_search_angle": state.get("next_search_angle", ""),
        "suggested_query_terms": list(state.get("suggested_query_terms") or []),
        "avoid_query_terms": list(state.get("avoid_query_terms") or []),
        "steps_json": list(state.get("steps_json") or []),
        "chunks_by_query_json": list(state.get("chunks_by_query_json") or []),
        "messages": list(state.get("messages") or []),
    }
    search_count_delta = 0
    llm_call_delta = 0
    evidence_flags: list[bool] = []

    # void_slot_attempts persisted across rounds in state
    void_slot_attempts: dict[str, int] = dict(state.get("void_slot_attempts") or {})

    def _merge_result(result: dict) -> None:
        """Merge a slot result into accumulated (called under lock)."""
        from .graph_utils import (
            _merge_evidence_dict, _merge_unique_list, _merge_dict_overwrite,
        )
        nonlocal search_count_delta, llm_call_delta
        result.pop("_last_quality", None)

        if "slot_status" in result:
            accumulated["slot_status"] = _merge_dict_overwrite(
                accumulated["slot_status"], result["slot_status"]
            )
        for list_key in ("used_queries", "steps_json", "chunks_by_query_json", "messages"):
            if list_key in result:
                accumulated[list_key] = accumulated[list_key] + list(result[list_key] or [])
        for unique_key in ("sources", "known_keywords", "used_query_keys", "seen_chunk_keys",
                           "suggested_query_terms", "avoid_query_terms"):
            if unique_key in result:
                accumulated[unique_key] = _merge_unique_list(
                    accumulated[unique_key], result[unique_key]
                )
        for ev_key in ("evidence", "evidence_details"):
            if ev_key in result:
                accumulated[ev_key] = _merge_evidence_dict(accumulated[ev_key], result[ev_key])
        for str_key in ("last_reflection", "next_search_angle"):
            if result.get(str_key):
                accumulated[str_key] = result[str_key]
        search_count_delta += int(result.get("search_count", 0))
        llm_call_delta += int(result.get("llm_call_count", 0))

    async def run_one(item: dict) -> None:
        slot = item["slot"]
        use_verification = item.get("use_verification_query", False)

        async with semaphore:
            async with lock:
                current_state = {**state, **accumulated}

            result = await _run_single_slot_with_retry(
                slot=slot,
                hint=item.get("hint", ""),
                state=current_state,
                config=config,
                use_verification_query=use_verification,
            )

            quality = result.get("_last_quality", "UNKNOWN")
            has_evidence = bool(
                result.get("evidence", {}).get(slot) or
                any(v for v in (result.get("evidence") or {}).values())
            )

            async with lock:
                if quality in ("NO_RESULTS", "NOT_USEFUL") and not has_evidence:
                    void_slot_attempts[slot] = void_slot_attempts.get(slot, 0) + 1
                    count = void_slot_attempts[slot]

                    if count >= MAX_VOID_TOTAL:
                        # Decide final status based on required flag
                        coverage_item = next(
                            (c for c in (state.get("coverage_items") or []) if c.get("id") == slot),
                            {"required": True},
                        )
                        new_status = "NOT_FOUND" if coverage_item.get("required", True) else "OMITTED"
                        accumulated["slot_status"][slot] = new_status
                        evidence_flags.append(False)
                        if on_stage:
                            try:
                                on_stage(f"找不到資料：{_slot_label(current_state, slot)} → {new_status}")
                            except Exception:
                                pass
                    else:
                        # Void but not final — will be retried next scheduler round
                        evidence_flags.append(False)
                else:
                    # Valid result
                    evidence_flags.append(bool(has_evidence or quality not in ("NO_RESULTS", "NOT_USEFUL")))
                    _merge_result(result)

                    if on_stage:
                        try:
                            status = accumulated["slot_status"].get(slot, "")
                            on_stage(f"完成搜尋「{_slot_label(current_state, slot)}」：{quality} / {status}")
                        except Exception:
                            pass

    _safe_update_current_observation(
        input={
            "pending_slots": [item["slot"] for item in pending],
            "search_count": state.get("search_count", 0),
        }
    )

    async with asyncio.TaskGroup() as tg:
        for item in pending:
            tg.create_task(run_one(item))

    found = any(evidence_flags)
    _safe_update_current_observation(
        output={
            "coverage_status": accumulated["slot_status"],
            "search_count_delta": search_count_delta,
            "void_slot_attempts": void_slot_attempts,
        }
    )

    return {
        **accumulated,
        "search_count": search_count_delta,
        "llm_call_count": llm_call_delta,
        "consecutive_no_new": 0 if found else state.get("consecutive_no_new", 0) + 1,
        "pending_slots": [],
        "void_slot_attempts": void_slot_attempts,
    }


# ── writer_node ───────────────────────────────────────────────────────────────

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
        # NOT_FOUND slots are expected to have "not found" language — skip label check for them
        not_found_labels = {
            rs.coverage_label(slot)
            for slot in rs.required_coverage_ids()
            if state.get("slot_status", {}).get(slot) in ("NOT_FOUND", "OMITTED")
        }
        required_check = required_labels - not_found_labels
        return bool(
            w.answer
            and len(w.answer.strip()) >= 150
            and all(label in w.answer for label in required_check)
        )

    writeup: ResearchWriteup = await write_summary(llm, rs)
    llm_calls = 1

    if not _answer_ok(writeup):
        missing = [label for label in required_labels if label not in (writeup.answer or "")]
        feedback = f"缺少以下必要段落：{', '.join(missing)}" if missing else "答案長度不足（需至少 150 字）"
        logger.warning(
            "writer_node: quality gate failed (%d chars), retrying: %s",
            len(writeup.answer or ""), feedback,
        )
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
