"""Utility helpers for the research graph — no LangGraph node logic."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata

from observability import update_current_observation_io

from .planner import PlannerDecision, build_slot_decision
from .state import (
    ResearchGraphState,
    ResearchState,
    _merge_dict_overwrite,
    _merge_evidence_dict,
    _merge_unique_list,
)

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
    for pattern in (r"[A-Za-z][A-Za-z0-9_\-]{1,24}", r"[一-鿿]{2,8}"):
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
        elif key in {"used_queries", "steps_json", "chunks_by_query_json", "messages"}:
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
        verification_done=False,  # removed from graph state
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

