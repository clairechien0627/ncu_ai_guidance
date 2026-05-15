"""LangGraph runtime for document research tasks."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import unicodedata
from typing import Literal

from langchain_core.runnables import RunnableConfig
from langfuse import observe
from langgraph.errors import NodeError
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langgraph.types import Command, RetryPolicy, Send, TimeoutPolicy

from observability import update_current_observation_io

from .orchestrator import decide_next_batch, get_candidate_slots
from .planner import PlannerDecision, build_slot_decision, plan_query_for_slot, should_use_hyde
from .reflector import apply_reflection, reflect_results
from .retriever import retrieve_evidence
from .state import (
    ResearchGraphState,
    ResearchState,
    SearchStep,
    WorkerState,
    _FLAGS_CLEAR,
    _merge_dict_overwrite,
    _merge_evidence_dict,
    _merge_unique_list,
)
from .writer import ResearchWriteup, _fallback_writeup, write_summary

logger = logging.getLogger(__name__)

_COMMON_TERMS = {
    "research",
    "method",
    "result",
    "summary",
    "document",
    "analysis",
    "background",
    "purpose",
    "pdf",
}
_MAX_STATE_KEYWORDS = 80
_TRACE_CHUNK_LIMIT = 900
_TRACE_CHUNKS_PER_QUERY = 4


def _normalize_query(q: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", str(q or "")).split())


def _seed_keywords(text: str) -> list[str]:
    candidates: list[str] = []

    def add(item: str) -> None:
        item = item.strip(" \t\r\n_-.,;:()[]{}\"'")
        if len(item) < 2 or item.lower() == "pdf":
            return
        if item.lower() in _COMMON_TERMS:
            return
        if re.fullmatch(r"[\d\-_.]+", item):
            return
        if item not in candidates:
            candidates.append(item)

    for keyword_block in re.findall(r"(?:keywords?|關鍵詞|關鍵字)\s*[:：]\s*([^\n]+)", text, flags=re.I):
        for term in re.split(r"[,;、，\s]+", keyword_block):
            add(term)
            if len(candidates) >= 24:
                return candidates
    for pattern in (r"[A-Za-z][A-Za-z0-9_\-]{1,24}", r"[\u4e00-\u9fff]{2,8}"):
        for match in re.findall(pattern, text):
            add(match)
            if len(candidates) >= 24:
                return candidates
    return candidates


def _cap_list(items: list, limit: int) -> list:
    return items if len(items) <= limit else items[:limit]


def _compact_chunk(chunk: dict) -> dict:
    return {
        "filename": chunk.get("filename", ""),
        "page": chunk.get("page"),
        "page_end": chunk.get("page_end"),
        "section": chunk.get("section"),
        "content": str(chunk.get("content", ""))[:_TRACE_CHUNK_LIMIT],
        "is_low_quality": bool(chunk.get("is_low_quality", False)),
    }


def _compact_chunks(chunks: list[dict]) -> list[dict]:
    return [_compact_chunk(chunk) for chunk in chunks[:_TRACE_CHUNKS_PER_QUERY]]


def _compact_evidence(evidence: dict[str, list[str]], limit: int = 8) -> dict[str, list[str]]:
    return {slot: notes[:limit] for slot, notes in evidence.items()}


def _compact_evidence_details(evidence: dict[str, list[dict]], limit: int = 8) -> dict[str, list[dict]]:
    return {slot: notes[:limit] for slot, notes in evidence.items()}


def _evidence_counts(state: ResearchGraphState | ResearchState) -> dict[str, int]:
    evidence = state.evidence if isinstance(state, ResearchState) else state.get("evidence", {})
    return {slot: len(notes or []) for slot, notes in evidence.items()}


def _coverage_status_for_trace(state: ResearchGraphState | ResearchState) -> dict[str, str]:
    statuses = state.slot_status if isinstance(state, ResearchState) else state.get("slot_status", {})
    return {str(slot): str(status) for slot, status in statuses.items()}


def _safe_update_current_observation(*, input=None, output=None, metadata: dict | None = None) -> None:
    update_current_observation_io(input=input, output=output, metadata=metadata)


def _slot_label(state: ResearchGraphState | ResearchState | dict, slot: str) -> str:
    items = state.coverage_items if isinstance(state, ResearchState) else state.get("coverage_items", [])
    for item in items or []:
        if item.get("id") == slot:
            return str(item.get("label") or slot)[:16]
    return slot.replace("_", " ")


def _public_steps(state: ResearchGraphState) -> list[dict]:
    return [
        {
            "round": index + 1,
            "slot": step.get("slot"),
            "slot_label": _slot_label(state, str(step.get("slot") or "")),
            "display_intent": step.get("display_intent"),
            "quality": step.get("quality"),
            "chunk_count": step.get("chunk_count", 0),
            "updated_slots": step.get("updated_slots", []),
            "missing_gap": step.get("missing_gap"),
            "next_search_angle": step.get("next_search_angle"),
        }
        for index, step in enumerate(state.get("steps_json", []))
    ]


def _trace_summary(state: ResearchGraphState) -> dict:
    return {
        "task": {
            "question": state.get("question", ""),
            "document_ids": state.get("document_ids", []),
            "task_type": state.get("metadata", {}).get("task_type"),
            "route_intent": state.get("metadata", {}).get("route_intent"),
            "goal": state.get("task_goal", ""),
        },
        "limits": {
            "effective_max_searches": _hard_max_searches(state),
            "max_searches_per_slot": _per_slot_cap(state),
            "max_consecutive_no_new": state.get("max_consecutive_no_new", 4),
        },
        "coverage": [
            {
                "id": item.get("id"),
                "label": item.get("label") or item.get("id"),
                "required": item.get("required", True),
                "status": state.get("slot_status", {}).get(item.get("id"), "NOT_FILLED"),
                "evidence_count": len(state.get("evidence", {}).get(item.get("id"), [])),
                "evidence_detail_count": len(state.get("evidence_details", {}).get(item.get("id"), [])),
            }
            for item in state.get("coverage_items", [])
        ],
        "progress": {
            "search_count": state.get("search_count", 0),
            "verification_done": state.get("verification_done", False),
            "slot_status": state.get("slot_status", {}),
        },
        "steps": _public_steps(state),
        "final": {
            "sources": state.get("final_sources", []),
            "answer_preview": str(state.get("final_answer", ""))[:500],
        },
    }


def _clean_query_term(term: str) -> str:
    term = _normalize_query(term).strip(" \t\r\n_-.,;:()[]{}\"'")
    if not term:
        return ""
    lower = term.lower()
    blocked = ("search for", "describe", "provide", "research_", "coverage", "expected_evidence", "slot")
    if any(token in lower for token in blocked):
        return ""
    if len(term) > 48:
        return ""
    return term


def _per_slot_cap(state: ResearchGraphState) -> int:
    try:
        return max(1, int(state.get("max_searches_per_slot") or 7))
    except Exception:
        return 7


def _hard_max_searches(state: ResearchGraphState) -> int:
    required_count = len([item for item in state.get("coverage_items", []) if item.get("required", True)])
    required_count = required_count or len(state.get("coverage_items", [])) or 1
    derived = required_count * _per_slot_cap(state) + 1
    try:
        configured = int(state.get("max_searches") or 0)
    except Exception:
        configured = 0
    return max(configured, derived)


def _slot_search_counts(state: ResearchGraphState) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in state.get("coverage_items", []):
        item_id = str(item.get("id") or "").strip()
        if item_id:
            counts.setdefault(item_id, 0)
    for step in state.get("steps_json", []):
        slot = str(step.get("slot") or "").strip()
        if slot:
            counts[slot] = counts.get(slot, 0) + 1
    return counts


def _slot_uses_hyde(rs: ResearchState, slot: str) -> bool:
    return bool(rs.coverage_item(slot).get("use_hyde", False))


def _next_coverage_slot(state: ResearchGraphState, rs: ResearchState) -> str | None:
    counts = _slot_search_counts(state)
    cap = _per_slot_cap(state)
    required_order = rs.required_coverage_ids()
    required = [
        slot
        for slot in required_order
        if rs.slot_status.get(slot, "NOT_FILLED") not in ("FILLED", "EXHAUSTED")
        and counts.get(slot, 0) < cap
    ]
    if not required:
        return None
    untried = [slot for slot in required if counts.get(slot, 0) == 0]
    if untried:
        candidates = [
            slot
            for slot in untried
            if rs.slot_status.get(slot, "NOT_FILLED") == "NOT_FILLED" and not _slot_uses_hyde(rs, slot)
        ]
        if candidates:
            return candidates[0]
        candidates = [slot for slot in untried if not _slot_uses_hyde(rs, slot)]
        if candidates:
            return candidates[0]
        return untried[0]
    recent = list(state.get("steps_json", []))[-2:]
    stalled_slot = ""
    if len(recent) == 2 and recent[0].get("slot") == recent[1].get("slot"):
        if all(step.get("quality") in ("NO_RESULTS", "NOT_USEFUL") for step in recent):
            stalled_slot = str(recent[-1].get("slot") or "")
    status_rank = {"NOT_FILLED": 0, "PARTIAL": 1, "EXHAUSTED": 2, "FILLED": 3}
    ranked = sorted(
        required,
        key=lambda slot: (
            slot == stalled_slot,
            _slot_uses_hyde(rs, slot),
            status_rank.get(rs.slot_status.get(slot, "NOT_FILLED"), 0),
            counts.get(slot, 0),
            required_order.index(slot),
        ),
    )
    return ranked[0] if ranked else None


def _required_slots_without_direct_search(state: ResearchGraphState, rs: ResearchState) -> list[str]:
    counts = _slot_search_counts(state)
    return [slot for slot in rs.required_coverage_ids() if counts.get(slot, 0) == 0]


def _ready_for_verification(state: ResearchGraphState, rs: ResearchState) -> bool:
    return rs.ready_for_verification() and not _required_slots_without_direct_search(state, rs)


def _verification_display_intent(rs: ResearchState, slot: str) -> str:
    return f"補強{rs.coverage_label(slot)}證據"


def _verification_section_terms(rs: ResearchState, slot: str) -> list[str]:
    item = rs.coverage_item(slot)
    terms = [rs.coverage_label(slot), *(item.get("search_hints") or [])]
    out: list[str] = []
    for term in terms:
        cleaned = _clean_query_term(str(term))
        if cleaned and cleaned not in out:
            out.append(cleaned)
        if len(out) >= 6:
            break
    return out


def _verification_query(rs: ResearchState, slot: str) -> str:
    terms = _verification_section_terms(rs, slot)
    return _normalize_query(" ".join(terms[:6])) or rs.coverage_label(slot)


def _query_for_slot(rs: ResearchState, slot: str) -> str:
    item = rs.coverage_item(slot)
    terms: list[str] = []
    for raw in [*(item.get("search_hints") or []), *rs.suggested_query_terms, *rs.known_keywords]:
        term = _clean_query_term(str(raw))
        if term and term not in terms and term not in rs.avoid_query_terms:
            terms.append(term)
        if len(terms) >= 5:
            break
    if terms:
        return _normalize_query(" ".join(terms))
    description = _clean_query_term(str(item.get("description") or ""))
    return description or slot


def _force_decision_slot(decision: PlannerDecision, rs: ResearchState, slot: str) -> PlannerDecision:
    if decision.next_slot == slot:
        return decision
    return build_slot_decision(
        rs,
        slot,
        rationale=f"{decision.rationale} | scheduler selected another eligible coverage slot",
    )


def _force_next_angle_if_stalled(decision: PlannerDecision, state: ResearchGraphState) -> PlannerDecision:
    last = next(
        (step for step in reversed(state.get("steps_json", [])) if step.get("slot") == decision.next_slot),
        None,
    )
    if not last or last.get("quality") not in ("NOT_USEFUL", "NO_RESULTS"):
        return decision
    angle = _normalize_query(str(last.get("next_search_angle") or ""))
    if not angle:
        return decision
    return decision.model_copy(update={"keyword_query": angle})


def _unique_query(query: str, state: ResearchState, slot: str) -> str:
    cleaned = _normalize_query(query)
    used = {_normalize_query(q) for q in state.used_queries}
    if cleaned and cleaned not in used:
        return cleaned
    for term in [*state.suggested_query_terms, *state.known_keywords, state.next_search_angle]:
        cleaned_term = _clean_query_term(term)
        if cleaned_term and cleaned_term not in cleaned and cleaned_term not in state.avoid_query_terms:
            candidate = _normalize_query(f"{cleaned} {cleaned_term}")
            if candidate and candidate not in used:
                return candidate
    candidate = _verification_query(state, slot) or _normalize_query(slot)
    if candidate and candidate not in used:
        return candidate
    return cleaned or slot


def _query_contains_meta_instruction(query: str) -> bool:
    lower = query.lower()
    return any(token in lower for token in ("research_", "coverage", "expected_evidence", "missing_gap"))


def _force_unique_decision(decision: PlannerDecision, state: ResearchState) -> PlannerDecision:
    raw_query = decision.keyword_query
    if _query_contains_meta_instruction(raw_query):
        raw_query = _query_for_slot(state, decision.next_slot)
    query = _unique_query(raw_query, state, decision.next_slot)
    return decision.model_copy(update={"keyword_query": query})


def _reserve_query(query: str, used_keys: set[str], slot: str, count: int) -> str:
    candidate = _normalize_query(query)
    if candidate in used_keys:
        for alternative in (
            _normalize_query(f"{query} evidence"),
            _normalize_query(f"{slot} round {count + 1} evidence"),
        ):
            if alternative not in used_keys:
                candidate = alternative
                break
    used_keys.add(candidate)
    return candidate


def _chunk_key(chunk: dict) -> str:
    return hashlib.md5(str(chunk.get("content", "")).encode("utf-8", errors="replace")).hexdigest()


def _merge_stream_patch(state: dict, patch: dict) -> dict:
    merged = dict(state)
    for key, value in patch.items():
        if key in {"known_keywords", "sources", "seen_chunk_keys", "used_query_keys"}:
            merged[key] = _merge_unique_list(merged.get(key, []), value)
        elif key in {"evidence", "evidence_details"}:
            merged[key] = _merge_evidence_dict(merged.get(key, {}), value)
        elif key == "slot_status":
            merged[key] = _merge_dict_overwrite(merged.get(key, {}), value)
        elif key in {"used_queries", "steps_json", "chunks_by_query_json", "messages", "_batch_evidence_flags"}:
            merged[key] = list(merged.get(key, [])) + list(value or [])
        elif key in {"search_count", "llm_call_count"}:
            merged[key] = int(merged.get(key, 0) or 0) + int(value or 0)
        else:
            merged[key] = value
    return merged


def _graph_to_rs(state: ResearchGraphState) -> ResearchState:
    return ResearchState(
        question=state["question"],
        document_ids=list(state["document_ids"]),
        document_context=state["document_context"],
        task_goal=state.get("task_goal", ""),
        coverage_items=list(state.get("coverage_items", [])),
        output_contract=state.get("output_contract", ""),
        search_count=state["search_count"],
        consecutive_no_new=state["consecutive_no_new"],
        verification_done=state["verification_done"],
        known_keywords=list(state["known_keywords"]),
        used_queries=list(state["used_queries"]),
        slot_status=dict(state["slot_status"]),
        evidence={slot: list(notes) for slot, notes in state["evidence"].items()},
        evidence_details={slot: list(notes) for slot, notes in state.get("evidence_details", {}).items()},
        sources=list(state["sources"]),
        last_reflection=state.get("last_reflection", ""),
        next_search_angle=state.get("next_search_angle", ""),
        suggested_query_terms=list(state.get("suggested_query_terms", [])),
        avoid_query_terms=list(state.get("avoid_query_terms", [])),
    )


def _tool_result_content(chunks: list[dict]) -> str:
    return json.dumps(
        {
            "result_count": len(chunks),
            "chunks": [
                {
                    "filename": chunk.get("filename"),
                    "page": chunk.get("page"),
                    "page_end": chunk.get("page_end"),
                    "section": chunk.get("section"),
                    "content": str(chunk.get("content", ""))[:_TRACE_CHUNK_LIMIT],
                }
                for chunk in chunks[:_TRACE_CHUNKS_PER_QUERY]
            ],
        },
        ensure_ascii=False,
    )


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


def should_continue(state: ResearchGraphState) -> Literal["orchestrator", "writer", "verification"]:
    if state["search_count"] >= _hard_max_searches(state):
        return "writer"
    rs = _graph_to_rs(state)
    min_ev = state.get("min_evidence_per_slot", 0)
    if rs.done(min_evidence_per_slot=min_ev):
        return "writer"
    if not state["verification_done"] and _ready_for_verification(state, rs):
        return "verification"
    max_no_new = state.get("max_consecutive_no_new", 4)
    if state["consecutive_no_new"] >= max_no_new:
        if not _required_slots_without_direct_search(state, rs):
            return "writer"
    if not get_candidate_slots(state, rs, _slot_search_counts(state), _per_slot_cap(state)):
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


def _build(checkpointer=False) -> object:
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
    return builder.compile(checkpointer=checkpointer)


research_graph = _build()

_research_graph_checkpointed: object | None = None


def get_or_build_research_graph(checkpointer=None) -> object:
    global _research_graph_checkpointed
    if checkpointer is None:
        return research_graph
    if _research_graph_checkpointed is None:
        _research_graph_checkpointed = _build(checkpointer=checkpointer)
    return _research_graph_checkpointed


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
