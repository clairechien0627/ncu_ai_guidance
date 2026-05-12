"""LangGraph runtime for document research tasks."""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from typing import Literal

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from .planner import PlannerDecision, _fallback_decision, build_slot_decision, plan_next_query, should_use_hyde
from .reflector import apply_reflection, reflect_results
from .retriever import retrieve_evidence
from .state import ResearchGraphState, ResearchState, SearchStep
from .writer import ResearchWriteup, write_summary

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
    """Extract lightweight seed terms from document abstracts or chunks."""
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
        for term in re.split(r"[,;，、；\s]+", keyword_block):
            add(term)
            if len(candidates) >= 24:
                return candidates

    patterns = (
        r"《[^》]{1,24}》",
        r"〈[^〉]{1,24}〉",
        r"[A-Za-z][A-Za-z0-9_\-]{1,24}",
        r"[一-鿿]{2,8}",
    )
    for pattern in patterns:
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


def _slot_label(state: ResearchGraphState | ResearchState, slot: str) -> str:
    if isinstance(state, ResearchState):
        return state.coverage_label(slot)
    for item in state.get("coverage_items", []) or []:
        if item.get("id") == slot:
            return str(item.get("label") or slot)
    return slot.replace("_", " ")


def _trace_summary(state: ResearchGraphState) -> dict:
    steps = state.get("steps_json", [])
    return {
        "task": {
            "question": state.get("question", ""),
            "document_ids": state.get("document_ids", []),
            "mode": state.get("metadata", {}).get("mode"),
            "goal": state.get("task_goal", ""),
        },
        "limits": {
            "effective_max_searches": _hard_max_searches(state),
            "max_searches_per_slot": _per_slot_cap(state),
            "max_consecutive_no_new": state.get("max_consecutive_no_new", 2),
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
        "steps": [
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
                "query_bundle": {
                    "keyword_query": step.get("keyword_query"),
                    "semantic_query": step.get("semantic_query"),
                    "section_terms": step.get("section_terms", []),
                    "use_hyde": step.get("use_hyde", False),
                },
            }
            for index, step in enumerate(steps)
        ],
        "final": {
            "sources": state.get("final_sources", []),
            "answer_preview": str(state.get("final_answer", ""))[:500],
        },
    }


def _public_steps(state: ResearchGraphState) -> list[dict]:
    """Small step log for root LangSmith fields; full queries stay in trace_summary."""
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


def _clean_query_term(term: str) -> str:
    term = _normalize_query(term).strip(" \t\r\n_-.,;:()[]{}\"'")
    if not term:
        return ""
    lower = term.lower()
    blocked = (
        "search for",
        "describe",
        "provide",
        "success",
        "criteria",
        "evidence",
        "output",
        "contract",
        "research_",
        "coverage",
        "slot",
        "query",
    )
    if any(token in lower for token in blocked):
        return ""
    if len(term) > 28 or len(term.split()) > 4:
        return ""
    if lower in _COMMON_TERMS:
        return ""
    return term


def _verification_query(state: ResearchState, slot: str) -> str:
    item = state.coverage_item(slot)
    terms: list[str] = []
    for raw in [
        *(item.get("search_hints") or []),
        *state.suggested_query_terms,
        *state.known_keywords,
    ]:
        term = _clean_query_term(str(raw))
        if term and term not in terms and term not in state.avoid_query_terms:
            terms.append(term)
        if len(terms) >= 5:
            break
    suffix = ["研究限制", "未明示", "不足", "結論"] if "limit" in slot else ["研究成果", "證據", "結論"]
    return _normalize_query(" ".join([*terms, *suffix]))


def _per_slot_cap(state: ResearchGraphState) -> int:
    try:
        return max(1, int(state.get("max_searches_per_slot") or 7))
    except Exception:
        return 7


def _hard_max_searches(state: ResearchGraphState) -> int:
    required_count = len([
        item for item in state.get("coverage_items", []) if item.get("required", True)
    ]) or len(state.get("coverage_items", [])) or 1
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
        slot for slot in required_order
        if rs.slot_status.get(slot, "NOT_FILLED") not in ("FILLED", "EXHAUSTED")
        and counts.get(slot, 0) < cap
    ]
    if not required:
        return None

    # Prefer factual (non-HyDE) untried slots over interpretive (HyDE) ones so that
    # HyDE searches have prior evidence context available before they run.
    untried = [slot for slot in required if counts.get(slot, 0) == 0]
    if untried:
        # 1. Untried + NOT_FILLED + non-HyDE (highest priority)
        candidates = [
            s for s in untried
            if rs.slot_status.get(s, "NOT_FILLED") == "NOT_FILLED" and not _slot_uses_hyde(rs, s)
        ]
        if candidates:
            return candidates[0]

        # 2. Untried + non-HyDE (already got cross-slot evidence but never directly searched)
        candidates = [s for s in untried if not _slot_uses_hyde(rs, s)]
        if candidates:
            return candidates[0]

        # 3. Untried HyDE slots — only after all non-HyDE untried slots are done
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
            _slot_uses_hyde(rs, slot),           # HyDE slots ranked lower
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
    label = rs.coverage_label(slot)
    description = str(item.get("description") or "")
    text = f"{slot} {label} {description}"
    terms = [label, *(item.get("search_hints") or [])]
    if any(token in text for token in ("method", "方法", "步驟", "procedure")):
        terms.extend(["研究方法", "研究步驟", "分類歸納", "對比詮釋"])
    elif any(token in text for token in ("finding", "result", "成果", "發現", "例子", "分類")):
        terms.extend(["研究成果", "結論", "小結", "分類", "代表性例子"])
    elif any(token in text for token in ("limit", "限制", "不足", "未解決")):
        terms.extend(["研究限制", "未明示", "不足", "結論"])
    elif any(token in text for token in ("motivation", "動機", "目的", "背景")):
        terms.extend(["研究動機", "研究目的", "研究背景"])
    else:
        terms.extend(["結論", "討論", "證據"])

    out: list[str] = []
    for term in terms:
        cleaned = _clean_query_term(str(term)) or str(term).strip()
        if cleaned and cleaned not in out:
            out.append(cleaned)
        if len(out) >= 6:
            break
    return out


def _query_for_slot(rs: ResearchState, slot: str) -> str:
    item = rs.coverage_item(slot)
    terms: list[str] = []
    for raw in [
        *(item.get("search_hints") or []),
        *rs.suggested_query_terms,
        *rs.known_keywords,
    ]:
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
            _normalize_query(f"{query} 證據"),
            _normalize_query(f"{slot} 第{count + 1}輪 證據"),
        ):
            if alternative not in used_keys:
                candidate = alternative
                break
    used_keys.add(candidate)
    return candidate


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
        steps=[],
        last_reflection=state.get("last_reflection", ""),
        next_search_angle=state.get("next_search_angle", ""),
        suggested_query_terms=list(state.get("suggested_query_terms", [])),
        avoid_query_terms=list(state.get("avoid_query_terms", [])),
    )


def _rs_to_patch(rs: ResearchState) -> dict:
    return {
        "search_count": rs.search_count,
        "consecutive_no_new": rs.consecutive_no_new,
        "verification_done": rs.verification_done,
        "known_keywords": rs.known_keywords,
        "used_queries": rs.used_queries,
        "slot_status": rs.slot_status,
        "evidence": rs.evidence,
        "evidence_details": rs.evidence_details,
        "sources": rs.sources,
        "last_reflection": rs.last_reflection,
        "next_search_angle": rs.next_search_angle,
        "suggested_query_terms": rs.suggested_query_terms,
        "avoid_query_terms": rs.avoid_query_terms,
    }


def _tool_result_content(chunks: list[dict]) -> str:
    return json.dumps(
        {
            "results": [
                {
                    "filename": chunk.get("filename", ""),
                    "page": chunk.get("page"),
                    "page_end": chunk.get("page_end"),
                    "section": chunk.get("section"),
                    "content": str(chunk.get("content", ""))[:_TRACE_CHUNK_LIMIT],
                }
                for chunk in chunks[:_TRACE_CHUNKS_PER_QUERY]
            ]
        },
        ensure_ascii=False,
    )


async def planner_node(state: ResearchGraphState, config: RunnableConfig) -> dict:
    llm = config["configurable"]["llm"]
    on_stage = config["configurable"].get("on_stage")

    rs = _graph_to_rs(state)
    is_verification = _ready_for_verification(state, rs)
    scheduled_slot = _next_coverage_slot(state, rs)

    try:
        decision = await plan_next_query(llm, rs)
    except Exception as exc:
        logger.warning("planner_node LLM failed: %s", exc)
        decision = _fallback_decision(rs)

    if is_verification:
        weakest = rs.weakest_slot()
        verification_query = _verification_query(rs, weakest)
        label = rs.coverage_label(weakest)
        decision = decision.model_copy(
            update={
                "next_slot": weakest,
                "keyword_query": verification_query or decision.keyword_query,
                "display_intent": _verification_display_intent(rs, weakest),
                "section_terms": _verification_section_terms(rs, weakest),
                "thought": f"所有必要檢索項目已至少嘗試一次，現在補強「{label}」的薄弱證據。",
                "rationale": f"所有 required 檢索項目已至少嘗試，補一次最薄弱的「{label}」證據。",
                "use_hyde": should_use_hyde(rs, weakest, requested=decision.use_hyde),
            }
        )

    if not is_verification and scheduled_slot:
        decision = _force_decision_slot(decision, rs, scheduled_slot)

    decision = _force_unique_decision(decision, rs)
    used_keys: set[str] = set(state["used_query_keys"])
    final_query = _reserve_query(decision.keyword_query, used_keys, decision.next_slot, state["search_count"])
    decision = decision.model_copy(update={"keyword_query": final_query})

    tool_call_id = f"research_search_{state['search_count'] + 1}"

    if on_stage:
        try:
            on_stage(f"規劃搜尋（第 {state['search_count'] + 1} 輪）：{decision.display_intent}")
        except Exception:
            pass

    current_step = {
        "slot": decision.next_slot,
        "query": final_query,
        "keyword_query": final_query,
        "semantic_query": decision.semantic_query,
        "section_terms": decision.section_terms,
        "use_hyde": decision.use_hyde,
        "display_intent": decision.display_intent,
        "tool_call_id": tool_call_id,
        "thought": decision.thought,
        "expected_evidence": decision.expected_evidence,
        "planner_rationale": decision.rationale,
        "is_verification": is_verification,
    }

    return {
        "current_step": current_step,
        "used_query_keys": list(used_keys),
        "messages": [{
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
                    "slot": decision.next_slot,
                    "display_intent": decision.display_intent,
                    "expected_evidence": decision.expected_evidence,
                },
            }],
        }],
        "llm_call_count": state["llm_call_count"] + 1,
    }


async def retriever_node(state: ResearchGraphState, config: RunnableConfig) -> dict:
    step = state["current_step"]
    seen: set[str] = set(state["seen_chunk_keys"])

    chunks, sources = await retrieve_evidence(
        query=step.get("query", ""),
        display_intent=step.get("display_intent", ""),
        keyword_query=step.get("keyword_query", ""),
        semantic_query=step.get("semantic_query", ""),
        section_terms=step.get("section_terms", []),
        use_hyde=bool(step.get("use_hyde", False)),
        document_ids=state["document_ids"],
        seen_chunks=seen,
        on_stage=None,
        search_count=state["search_count"],
        consecutive_empty=state["consecutive_no_new"],
        max_searches=_hard_max_searches(state),
        max_consecutive_empty=state["max_consecutive_no_new"],
        mode=str(state["metadata"].get("mode") or "research"),
    )

    merged_sources = list(state["sources"])
    for source in sources:
        if source not in merged_sources:
            merged_sources.append(source)

    chunk_text = "\n".join(str(chunk.get("content", ""))[:1200] for chunk in chunks)
    new_keywords = list(state["known_keywords"])
    for keyword in _seed_keywords(chunk_text):
        if keyword not in new_keywords:
            new_keywords.append(keyword)
    new_keywords = _cap_list(new_keywords, _MAX_STATE_KEYWORDS)

    used_queries = list(state["used_queries"])
    query = step.get("query", "")
    if query and query not in used_queries:
        used_queries.append(query)

    return {
        "current_chunks": chunks,
        "seen_chunk_keys": list(seen),
        "sources": merged_sources,
        "known_keywords": new_keywords,
        "used_queries": used_queries,
        "messages": [{
            "type": "tool",
            "name": "search_report",
            "tool_call_id": step.get("tool_call_id", ""),
            "content": _tool_result_content(chunks),
        }],
    }


async def reflector_node(state: ResearchGraphState, config: RunnableConfig) -> dict:
    llm = config["configurable"]["llm"]

    rs = _graph_to_rs(state)
    step = state["current_step"]
    chunks = state["current_chunks"]
    slot = step.get("slot", "")
    query = step.get("query", "")

    # Snapshot before apply so we can detect real evidence progress vs keyword-only updates
    status_before = dict(rs.slot_status)
    evidence_counts_before = {k: len(v) for k, v in rs.evidence.items()}

    reflection = await reflect_results(llm, state=rs, slot=slot, query=query, chunks=chunks)
    apply_reflection(rs, reflection)

    # Only reset consecutive_no_new when slot status improved OR evidence notes were added.
    # Keyword-only updates (new_keywords) are too weak a signal.
    evidence_progress = bool(chunks) and (
        rs.slot_status != status_before
        or any(
            len(rs.evidence.get(k, [])) > evidence_counts_before.get(k, 0)
            for k in rs.evidence
        )
    )
    useful = evidence_progress
    if useful:
        consecutive_no_new = 0
        same_slot_no_new = 0
    else:
        consecutive_no_new = state["consecutive_no_new"] + 1
        prior_same_no_new = 0
        for previous in reversed(state.get("steps_json", [])):
            if previous.get("slot") != slot:
                break
            if previous.get("quality") in ("NO_RESULTS", "NOT_USEFUL"):
                prior_same_no_new += 1
            else:
                break
        same_slot_no_new = prior_same_no_new + 1

    slot_status = dict(rs.slot_status)
    if same_slot_no_new >= state["max_consecutive_no_new"]:
        slot_status[slot] = "EXHAUSTED"
        # Do NOT reset consecutive_no_new here: EXHAUSTED means we failed to
        # fill the slot, not that we found new evidence. Resetting would hide
        # the fact that no useful content exists and keep the loop running.

    current_slot_count = _slot_search_counts(state).get(slot, 0) + 1
    if current_slot_count >= _per_slot_cap(state) and slot_status.get(slot) != "FILLED":
        slot_status[slot] = "EXHAUSTED"
    # Only reset consecutive_no_new on FILLED (genuine progress), not EXHAUSTED.
    if slot_status.get(slot) == "FILLED":
        consecutive_no_new = 0

    updated_slots = [update.item_id for update in reflection.updates if update.notes]
    step_dict = {
        "slot": slot,
        "query": query,
        "display_intent": step.get("display_intent", ""),
        "keyword_query": step.get("keyword_query", query),
        "semantic_query": step.get("semantic_query", ""),
        "section_terms": step.get("section_terms", []),
        "use_hyde": bool(step.get("use_hyde", False)),
        "thought": step.get("thought", ""),
        "expected_evidence": step.get("expected_evidence", ""),
        "planner_rationale": step.get("planner_rationale", ""),
        "quality": reflection.quality,
        "new_keywords": reflection.new_keywords,
        "note": reflection.rationale,
        "missing_gap": reflection.missing_gap,
        "next_search_angle": reflection.next_search_angle,
        "chunk_count": len(chunks),
        "updated_slots": updated_slots,
    }
    cbq_entry = {"step": step_dict, "chunks": _compact_chunks(chunks)}

    reflector_message = {
        "type": "ai",
        "content": (
            f"整理證據（{_slot_label(state, slot)}）：{reflection.quality}。"
            f"{reflection.rationale} 缺口：{reflection.missing_gap} "
            f"下一輪方向：{reflection.next_search_angle}"
        ),
    }

    patch = _rs_to_patch(rs)
    patch["known_keywords"] = _cap_list(patch.get("known_keywords", []), _MAX_STATE_KEYWORDS)
    patch["evidence"] = _compact_evidence(patch.get("evidence", {}), limit=12)
    patch["evidence_details"] = _compact_evidence_details(patch.get("evidence_details", {}), limit=12)
    patch.update({
        "slot_status": slot_status,
        "consecutive_no_new": consecutive_no_new,
        "verification_done": state["verification_done"] or bool(step.get("is_verification")),
        "search_count": state["search_count"] + 1,
        "llm_call_count": state["llm_call_count"] + 1,
        "steps_json": list(state["steps_json"]) + [step_dict],
        "chunks_by_query_json": list(state["chunks_by_query_json"]) + [cbq_entry],
        "messages": [reflector_message],
    })
    return patch


async def writer_node(state: ResearchGraphState, config: RunnableConfig) -> dict:
    llm = config["configurable"]["llm"]
    on_stage = config["configurable"].get("on_stage")

    if on_stage:
        try:
            on_stage("撰寫摘要")
        except Exception:
            pass

    rs = _graph_to_rs(state)
    writeup: ResearchWriteup = await write_summary(llm, rs)

    # Quality gate: if answer is suspiciously short or missing required slot labels,
    # fall back to an evidence-stitched answer rather than surfacing a garbled LLM output.
    required_labels = {rs.coverage_label(s) for s in rs.required_coverage_ids() if rs.coverage_label(s)}
    answer_ok = bool(
        writeup.answer
        and len(writeup.answer.strip()) >= 150
        and all(label in writeup.answer for label in required_labels)
    )
    if not answer_ok:
        logger.warning("writer_node: answer failed quality gate (len=%d), using evidence fallback",
                       len(writeup.answer) if writeup.answer else 0)
        from .writer import _fallback_writeup
        writeup = _fallback_writeup(rs)

    final_sources = writeup.sources if writeup.sources else rs.sources[:5]
    final_state = dict(state)
    final_state.update({
        "final_answer": writeup.answer,
        "final_sources": final_sources,
    })
    return {
        "final_answer": writeup.answer,
        "final_sources": final_sources,
        "messages": [{"type": "ai", "content": writeup.answer, "sources": final_sources}],
        "known_keywords": _cap_list(list(state.get("known_keywords", [])), _MAX_STATE_KEYWORDS),
        "evidence": _compact_evidence(state.get("evidence", {}), limit=12),
        "evidence_details": _compact_evidence_details(state.get("evidence_details", {}), limit=12),
        # clear in-flight fields
        "current_step": {},
        "current_chunks": [],
        "seen_chunk_keys": [],
        "used_query_keys": [],
        "steps_json": _public_steps(final_state),
        "chunks_by_query_json": [],
        "suggested_query_terms": _cap_list(list(state.get("suggested_query_terms", [])), 12),
        "avoid_query_terms": _cap_list(list(state.get("avoid_query_terms", [])), 12),
        "trace_summary": _trace_summary(final_state),
        "llm_call_count": state["llm_call_count"] + 1,
    }


def should_continue(state: ResearchGraphState) -> Literal["planner", "writer"]:
    if state["search_count"] >= _hard_max_searches(state):
        return "writer"
    rs = _graph_to_rs(state)
    min_ev = state.get("min_evidence_per_slot", 0)
    if rs.done(min_evidence_per_slot=min_ev):
        return "writer"
    # Allow one verification pass before applying consecutive_no_new gate, so
    # that at least one follow-up search runs on the weakest slot.
    if not state["verification_done"] and _ready_for_verification(state, rs):
        return "planner"
    # If consecutive rounds produced nothing new, document content is exhausted.
    max_no_new = state.get("max_consecutive_no_new", 2)
    if state["consecutive_no_new"] >= max_no_new:
        return "writer"
    if _next_coverage_slot(state, rs) is None:
        return "writer"
    return "planner"


def _build() -> object:
    builder = StateGraph(ResearchGraphState)
    builder.add_node("planner", planner_node)
    builder.add_node("search_report", retriever_node)
    builder.add_node("reflector", reflector_node)
    builder.add_node("writer", writer_node)

    builder.add_edge(START, "planner")
    builder.add_edge("planner", "search_report")
    builder.add_edge("search_report", "reflector")
    builder.add_conditional_edges(
        "reflector",
        should_continue,
        {"planner": "planner", "writer": "writer"},
    )
    builder.add_edge("writer", END)
    return builder.compile(checkpointer=False)


research_graph = _build()


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
        steps=[],
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
