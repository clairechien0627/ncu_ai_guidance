from __future__ import annotations

import json
import logging
import re

from langchain_core.messages import HumanMessage
from observability import ainvoke_traced_generation
from pydantic import BaseModel, Field

from .runtime_prompts import research_node_stack_metadata, research_node_system_messages
from .state import ResearchState

logger = logging.getLogger(__name__)


class PlannerDecision(BaseModel):
    thought: str = Field(description="Brief reasoning based on current evidence and gaps.")
    next_slot: str = Field(description="Coverage item id to improve next.")
    display_intent: str = Field(description="Short Traditional Chinese label for UI/job logs.")
    keyword_query: str = Field(description="Keyword-style query for sparse/BM25 retrieval.")
    semantic_query: str = Field(description="One concise semantic sentence for dense retrieval.")
    section_terms: list[str] = Field(description="Section or heading terms for locating evidence.")
    use_hyde: bool = Field(
        default=False,
        description=(
            "Whether to add one HyDE hypothetical passage for retrieval only. "
            "Use for derived/interpretive slots or after weak retrieval; do not use for exact section lookup."
        ),
    )
    expected_evidence: str = Field(description="What evidence this query is expected to find.")
    rationale: str = Field(description="Short reason for the query.")

    def query_bundle(self) -> dict:
        return {
            "display_intent": self.display_intent,
            "keyword_query": self.keyword_query,
            "semantic_query": self.semantic_query,
            "section_terms": self.section_terms,
            "use_hyde": self.use_hyde,
        }


_SECTION_TERMS = {
    "motivation": ["\u7814\u7a76\u52d5\u6a5f", "\u7814\u7a76\u80cc\u666f", "\u7814\u7a76\u76ee\u7684", "\u7814\u7a76\u554f\u984c", "\u7dd2\u8ad6"],
    "method": ["\u7814\u7a76\u65b9\u6cd5", "\u7814\u7a76\u6b65\u9a5f", "\u5206\u985e\u6b78\u7d0d", "\u6587\u672c\u5206\u6790", "\u5206\u6790\u7814\u7a76", "\u5c0d\u6bd4\u8a6e\u91cb"],
    "findings": ["\u7814\u7a76\u6210\u679c", "\u4e3b\u8981\u767c\u73fe", "\u7d50\u8ad6", "\u5c0f\u7d50", "\u5206\u985e", "\u60c5\u611f\u8868\u9054"],
    "limitations": ["\u7814\u7a76\u9650\u5236", "\u672a\u89e3\u6c7a\u554f\u984c", "\u4e0d\u8db3", "\u5c40\u9650", "\u9069\u7528\u7bc4\u570d", "\u7d50\u8ad6"],
}

_DERIVED_HYDE_TERMS = (
    "\u6709\u8da3",
    "\u8fa8\u8b58",
    "\u5c0e\u8b80",
    "\u8208\u8da3",
    "\u91cf\u8868",
    "\u7279\u8272",
    "\u4eae\u9ede",
    "\u5438\u5f15",
    "notable",
    "distinctive",
    "interesting",
    "student",
    "hook",
)


def _item_text(state: ResearchState, item_id: str) -> str:
    item = state.coverage_item(item_id)
    hints = item.get("search_hints") or []
    return " ".join(
        [
            str(item.get("description", "")),
            " ".join(str(h) for h in hints[:4]),
            str(item.get("success_criteria", "")),
        ]
    ).strip()


def _slot_kind(slot: str, item_text: str) -> str:
    text = f"{slot} {item_text}".lower()
    if "motivation" in text or any(term in item_text for term in ("\u52d5\u6a5f", "\u80cc\u666f", "\u76ee\u7684", "\u7814\u7a76\u554f\u984c")):
        return "motivation"
    if "method" in text or any(term in item_text for term in ("\u65b9\u6cd5", "\u6b65\u9a5f", "\u5206\u985e\u6b78\u7d0d", "\u6587\u672c\u5206\u6790", "\u5c0d\u6bd4\u8a6e\u91cb")):
        return "method"
    if "limit" in text or any(term in item_text for term in ("\u9650\u5236", "\u4f88\u9650", "\u5c40\u9650", "\u4e0d\u8db3", "\u672a\u89e3\u6c7a", "\u9069\u7528\u7bc4\u570d")):
        return "limitations"
    if any(token in text for token in ("result", "finding")) or any(term in item_text for term in ("\u6210\u679c", "\u767c\u73fe", "\u7d50\u8ad6", "\u898f\u5247", "\u5206\u985e")):
        return "findings"
    # No category matched \u2014 return "generic" so callers get empty section_terms
    # rather than forcing the wrong section hints onto an unrelated question.
    return "generic"


def is_derived_hyde_slot(state: ResearchState, slot: str) -> bool:
    item = state.coverage_item(slot)
    # Explicit flag on the coverage item takes priority over term matching
    if "use_hyde" in item:
        return bool(item["use_hyde"])
    label = state.coverage_label(slot)
    text = " ".join(
        [
            slot,
            label,
            str(item.get("description") or ""),
            " ".join(str(hint) for hint in (item.get("search_hints") or [])),
            str(item.get("success_criteria") or ""),
        ]
    ).lower()
    return any(term.lower() in text for term in _DERIVED_HYDE_TERMS)


def should_use_hyde(state: ResearchState, slot: str, requested: bool = False) -> bool:
    """Allow HyDE only when it can use prior search context or weak-retrieval context."""
    has_search_context = (
        state.search_count > 0
        or bool(state.next_search_angle)
        or any(state.evidence.get(item_id) for item_id in state.coverage_ids())
    )
    if not has_search_context:
        return False
    derived_slot = is_derived_hyde_slot(state, slot)
    weak_retrieval = state.consecutive_no_new > 0 or state.slot_status.get(slot) == "PARTIAL"
    return derived_slot or (requested and weak_retrieval)


def _section_terms_for_slot(slot: str, item_text: str) -> list[str]:
    return _SECTION_TERMS.get(_slot_kind(slot, item_text), [])


def _clean_term(term: str) -> str:
    term = " ".join(str(term or "").split()).strip(" \t\r\n_-.,;:()[]{}\"'")
    if not term:
        return ""
    lowered = term.lower()
    blocked = (
        "coverage",
        "expected_evidence",
        "success criteria",
        "query",
        "slot",
        "research_",
    )
    if any(token in lowered for token in blocked):
        return ""
    if len(term) > 36:
        return ""
    return term


def _dedupe(items: list[str], limit: int) -> list[str]:
    out: list[str] = []
    for raw in items:
        term = _clean_term(raw)
        if term and term not in out:
            out.append(term)
        if len(out) >= limit:
            break
    return out


def _terms_for_slot(state: ResearchState, slot: str) -> list[str]:
    item = state.coverage_item(slot)
    evidence_tail = " ".join(state.evidence.get(slot, [])[-3:])
    evidence_terms = re.findall(r"[\u4e00-\u9fff]{2,8}|[A-Za-z][A-Za-z0-9_\-]{2,24}", evidence_tail)
    raw_terms = [
        *(item.get("search_hints") or []),
        *state.suggested_query_terms,
        *evidence_terms,
        *state.known_keywords,
    ]
    return [
        term
        for term in _dedupe(raw_terms, 10)
        if term not in state.avoid_query_terms
    ]


def build_slot_decision(
    state: ResearchState,
    slot: str,
    *,
    rationale: str = "Fallback planner: build a role-separated QueryBundle from coverage hints and current evidence.",
) -> PlannerDecision:
    item_text = _item_text(state, slot)
    label = state.coverage_label(slot)
    section_terms = _section_terms_for_slot(slot, item_text)
    terms = _terms_for_slot(state, slot)
    if state.consecutive_no_new:
        terms = _dedupe([*section_terms, *terms, *state.known_keywords], 10)
    if not terms:
        terms = _dedupe([label, *section_terms, *state.known_keywords], 8)

    keyword_query = " ".join(terms[:8]) or label
    semantic_parts = [
        f"\u5728\u6587\u4ef6\u4e2d\u5c0b\u627e\u300c{label}\u300d\u9700\u8981\u7684\u76f4\u63a5\u8b49\u64da\u3002",
        item_text,
    ]
    evidence_tail = " ".join(state.evidence.get(slot, [])[-3:])
    if evidence_tail:
        semantic_parts.append(f"\u5df2\u77e5\u8b49\u64da\u8a5e\uff1a{evidence_tail[:180]}")
    if state.next_search_angle:
        semantic_parts.append(f"\u4e0b\u4e00\u8f2a\u61c9\u88dc\u5f37\uff1a{state.next_search_angle}")
    semantic_query = " ".join(part for part in semantic_parts if part).strip()[:260]
    return PlannerDecision(
        thought=f"\u512a\u5148\u88dc\u8db3\u300c{label}\u300d\u7684\u8b49\u64da\uff0c\u4f7f\u7528\u95dc\u9375\u8a5e\u3001\u8a9e\u610f\u53e5\u8207\u7ae0\u7bc0\u8a5e\u6df7\u5408\u6aa2\u7d22\u3002",
        next_slot=slot,
        display_intent=f"\u5c0b\u627e{label}",
        keyword_query=keyword_query,
        semantic_query=semantic_query or keyword_query,
        use_hyde=should_use_hyde(state, slot),
        section_terms=section_terms[:8],
        expected_evidence=item_text or f"\u652f\u6490\u300c{label}\u300d\u7684\u6587\u4ef6\u8b49\u64da\u3002",
        rationale=rationale,
    )


def _fallback_decision(state: ResearchState) -> PlannerDecision:
    return build_slot_decision(state, state.weakest_slot())


def _looks_generic(query: str, state: ResearchState) -> bool:
    compact = query.replace(" ", "").lower()
    if len(compact) <= 4:
        return True
    generic_terms = {
        "\u7814\u7a76",
        "\u65b9\u6cd5",
        "\u6210\u679c",
        "\u9650\u5236",
        "\u52d5\u6a5f",
        "\u7d50\u8ad6",
        "researchmethod",
        "results",
        "summary",
    }
    if compact in generic_terms:
        return True
    return query in state.used_queries


def _repair_bundle(decision: PlannerDecision, state: ResearchState, slot: str) -> PlannerDecision:
    fallback = build_slot_decision(state, slot, rationale=decision.rationale)
    display_intent = decision.display_intent.strip() or fallback.display_intent
    keyword_query = decision.keyword_query.strip() or fallback.keyword_query
    semantic_query = decision.semantic_query.strip() or fallback.semantic_query
    section_terms = _dedupe(decision.section_terms or fallback.section_terms, 8)

    if _looks_generic(keyword_query, state):
        keyword_query = fallback.keyword_query
    if not section_terms:
        section_terms = fallback.section_terms
    if not semantic_query or len(semantic_query) < 12:
        semantic_query = fallback.semantic_query

    return decision.model_copy(
        update={
            "next_slot": slot,
            "display_intent": display_intent[:32],
            "keyword_query": keyword_query,
            "semantic_query": semantic_query[:260],
            "section_terms": section_terms,
            "use_hyde": should_use_hyde(state, slot, requested=decision.use_hyde or fallback.use_hyde),
        }
    )


def _repair_query(decision: PlannerDecision, state: ResearchState) -> PlannerDecision:
    slot = decision.next_slot if decision.next_slot in state.coverage_ids() else state.weakest_slot()
    return _repair_bundle(decision, state, slot)


async def plan_query_for_slot(
    llm,
    state: ResearchState,
    slot: str,
    hint: str = "",
) -> PlannerDecision:
    assigned_slot = slot if slot in state.coverage_ids() else state.weakest_slot()
    planner = llm.with_structured_output(PlannerDecision, strict=True)
    try:
        messages = [
            *research_node_system_messages("research_planner"),
            HumanMessage(
                content=json.dumps(
                    {
                        "question": state.question,
                        "document_context": state.document_context,
                        "assigned_slot": assigned_slot,
                        "hint": hint,
                        "instruction": (
                            "assigned_slot was selected by the scheduler; "
                            "next_slot must exactly equal assigned_slot."
                        ),
                        "state": state.planner_prompt_dict(),
                    },
                    ensure_ascii=False,
                )
            ),
        ]
        decision: PlannerDecision = await ainvoke_traced_generation(
            planner,
            messages,
            prompt_name="research_planner",
            metadata={
                "task_type": "research_task",
                "agent_name": "research_agent",
                **research_node_stack_metadata("research_planner"),
            },
        )
    except Exception as exc:
        logger.warning("plan_query_for_slot failed (slot=%s count=%d): %s", assigned_slot, state.search_count, exc)
        decision = build_slot_decision(state, assigned_slot)

    if decision.next_slot != assigned_slot:
        decision = decision.model_copy(update={"next_slot": assigned_slot})
    if hint and not decision.keyword_query.strip():
        decision = decision.model_copy(update={"keyword_query": hint})
    if not decision.keyword_query.strip():
        decision = build_slot_decision(state, assigned_slot)
    return _repair_bundle(decision, state, assigned_slot)


async def plan_next_query(llm, state: ResearchState) -> PlannerDecision:
    planner = llm.with_structured_output(PlannerDecision, strict=True)
    try:
        messages = [
            *research_node_system_messages("research_planner"),
            HumanMessage(
                content=json.dumps(
                    {
                        "question": state.question,
                        "document_context": state.document_context,
                        "state": state.planner_prompt_dict(),
                    },
                    ensure_ascii=False,
                )
            ),
        ]
        decision: PlannerDecision = await ainvoke_traced_generation(
            planner,
            messages,
            prompt_name="research_planner",
            metadata={
                "task_type": "research_task",
                "agent_name": "research_agent",
                **research_node_stack_metadata("research_planner"),
            },
        )
    except Exception as exc:
        logger.warning("plan_next_query failed (count=%d): %s", state.search_count, exc)
        decision = _fallback_decision(state)

    if decision.next_slot not in state.coverage_ids():
        decision = decision.model_copy(update={"next_slot": state.weakest_slot()})
    if not decision.keyword_query.strip():
        decision = _fallback_decision(state)
    return _repair_query(decision, state)
