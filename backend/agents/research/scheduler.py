from __future__ import annotations

import json
import logging

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from observability import ainvoke_traced_generation
from prompting.registry import get as get_prompt

from .runtime_prompts import research_node_stack_metadata
from .state import ResearchGraphState, ResearchState

logger = logging.getLogger(__name__)


class SlotPriority(BaseModel):
    slot_id: str = Field(description="Coverage slot id.")
    hint: str = Field(default="", description="Concrete search direction for this slot.")


class SchedulerDecision(BaseModel):
    slots: list[SlotPriority] = Field(
        default_factory=list,
        description="All candidate slots ordered by search direction clarity (most ready first).",
    )
    rationale: str = Field(default="", description="Short reason for the ordering.")


def get_candidate_slots(
    state: ResearchGraphState,
    rs: ResearchState,
    slot_search_counts: dict[str, int],
    per_slot_cap: int,
) -> list[str]:
    return [
        slot
        for slot in rs.required_coverage_ids()
        if rs.slot_status.get(slot) not in ("FILLED", "EXHAUSTED", "NOT_FOUND", "OMITTED")
        and slot_search_counts.get(slot, 0) < per_slot_cap
    ]


def _build_scheduler_prompt(
    rs: ResearchState,
    candidates: list[str],
    void_slot_attempts: dict[str, int],
) -> list:
    slot_counts = {slot: len(rs.evidence.get(slot, [])) for slot in candidates}
    candidate_payload = [
        {
            "slot_id": slot,
            "label": rs.coverage_label(slot),
            "status": rs.slot_status.get(slot, "NOT_FILLED"),
            "evidence_count": slot_counts.get(slot, 0),
            "void_attempts": void_slot_attempts.get(slot, 0),
            "coverage_item": rs.coverage_item(slot),
        }
        for slot in candidates
    ]
    payload = {
        "question": rs.question,
        "task_goal": rs.task_goal,
        "output_contract": rs.output_contract,
        "candidate_slots": candidate_payload,
        "state": rs.planner_prompt_dict(),
        "response_contract": {
            "slots": (
                "ALL candidate slots ordered by search direction clarity. "
                "Put slots with clear search_hints or with existing evidence first. "
                "Slots with void_attempts > 0 should be ordered last. "
                "Return ALL candidates, not just a subset."
            ),
            "rationale": "short reason for the ordering",
        },
    }
    return [
        SystemMessage(content=get_prompt("research_scheduler")),
        HumanMessage(content=json.dumps(payload, ensure_ascii=False)),
    ]


def _fallback_ordering(candidate_slots: list[str]) -> SchedulerDecision:
    return SchedulerDecision(
        slots=[SlotPriority(slot_id=slot, hint="") for slot in candidate_slots],
        rationale="fallback",
    )


async def decide_slot_ordering(
    llm,
    rs: ResearchState,
    candidate_slots: list[str],
    void_slot_attempts: dict[str, int],
) -> SchedulerDecision:
    if not candidate_slots:
        return SchedulerDecision(slots=[], rationale="no candidates")
    try:
        structured = llm.with_structured_output(SchedulerDecision, strict=True, include_raw=True)
        raw_result = await ainvoke_traced_generation(
            structured,
            _build_scheduler_prompt(rs, candidate_slots, void_slot_attempts),
            prompt_name="research_scheduler",
            metadata={
                "task_type": "research_task",
                "route_intent": "research",
                "agent_name": "research_agent",
                **research_node_stack_metadata("research_scheduler"),
            },
        )
        if isinstance(raw_result, dict):
            parsing_error = raw_result.get("parsing_error")
            if parsing_error:
                raw_msg = raw_result.get("raw")
                raw_content = getattr(raw_msg, "content", "") if raw_msg else ""
                logger.warning(
                    "decide_slot_ordering parse failed: %s | raw=%s",
                    parsing_error,
                    str(raw_content)[:500],
                )
                return _fallback_ordering(candidate_slots)
            decision: SchedulerDecision = raw_result["parsed"]
        else:
            decision: SchedulerDecision = raw_result
    except Exception as exc:
        logger.warning("decide_slot_ordering failed: %s", exc)
        return _fallback_ordering(candidate_slots)

    allowed = set(candidate_slots)
    slots: list[SlotPriority] = []
    seen: set[str] = set()
    for item in decision.slots:
        slot = str(item.slot_id or "").strip()
        if slot in allowed and slot not in seen:
            slots.append(SlotPriority(slot_id=slot, hint=(item.hint or "").strip()))
            seen.add(slot)
    # Append any candidates the LLM missed (preserve all candidates)
    for slot in candidate_slots:
        if slot not in seen:
            slots.append(SlotPriority(slot_id=slot, hint=""))
    if not slots:
        return _fallback_ordering(candidate_slots)
    return SchedulerDecision(slots=slots, rationale=decision.rationale)
