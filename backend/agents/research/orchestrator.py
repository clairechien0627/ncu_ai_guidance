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


MAX_BATCH_SLOTS = 3


class SlotReadiness(BaseModel):
    slot_id: str = Field(description="Coverage slot id to search in this batch.")
    hint: str = Field(default="", description="Concrete search direction for this slot.")


class OrchestratorDecision(BaseModel):
    slots: list[SlotReadiness] = Field(
        default_factory=list,
        description="Slots to dispatch now, at most three.",
    )
    rationale: str = Field(default="", description="Short reason for the batch decision.")


def get_candidate_slots(
    state: ResearchGraphState,
    rs: ResearchState,
    slot_search_counts: dict[str, int],
    per_slot_cap: int,
) -> list[str]:
    return [
        slot
        for slot in rs.required_coverage_ids()
        if rs.slot_status.get(slot) not in ("FILLED", "EXHAUSTED")
        and slot_search_counts.get(slot, 0) < per_slot_cap
    ]


def _build_orchestrator_prompt(rs: ResearchState, candidates: list[str]) -> list:
    slot_counts = {slot: len(rs.evidence.get(slot, [])) for slot in candidates}
    candidate_payload = [
        {
            "slot_id": slot,
            "label": rs.coverage_label(slot),
            "status": rs.slot_status.get(slot, "NOT_FILLED"),
            "evidence_count": slot_counts.get(slot, 0),
            "coverage_item": rs.coverage_item(slot),
        }
        for slot in candidates
    ]
    payload = {
        "question": rs.question,
        "task_goal": rs.task_goal,
        "output_contract": rs.output_contract,
        "candidate_slots": candidate_payload,
        "state": rs.as_prompt_dict(),
        "max_slots": MAX_BATCH_SLOTS,
        "response_contract": {
            "slots": "list of {slot_id, hint}; choose only candidate slot_id values",
            "rationale": "short reason",
        },
    }
    return [
        SystemMessage(content=get_prompt("research_orchestrator")),
        HumanMessage(content=json.dumps(payload, ensure_ascii=False)),
    ]


def _fallback_decision(candidate_slots: list[str]) -> OrchestratorDecision:
    return OrchestratorDecision(
        slots=[SlotReadiness(slot_id=slot, hint="") for slot in candidate_slots[:2]],
        rationale="fallback",
    )


async def decide_next_batch(
    llm,
    rs: ResearchState,
    candidate_slots: list[str],
) -> OrchestratorDecision:
    if not candidate_slots:
        return OrchestratorDecision(slots=[], rationale="no candidates")
    try:
        structured = llm.with_structured_output(OrchestratorDecision, strict=True)
        decision: OrchestratorDecision = await ainvoke_traced_generation(
            structured,
            _build_orchestrator_prompt(rs, candidate_slots),
            prompt_name="research_orchestrator",
            metadata={
                "task_type": "research_task",
                "route_intent": "research",
                "agent_name": "research_agent",
                **research_node_stack_metadata("research_orchestrator"),
            },
        )
    except Exception as exc:
        logger.warning("decide_next_batch failed: %s", exc)
        return _fallback_decision(candidate_slots)

    allowed = set(candidate_slots)
    slots: list[SlotReadiness] = []
    seen: set[str] = set()
    for item in decision.slots:
        slot = str(item.slot_id or "").strip()
        if slot in allowed and slot not in seen:
            slots.append(SlotReadiness(slot_id=slot, hint=(item.hint or "").strip()))
            seen.add(slot)
        if len(slots) >= MAX_BATCH_SLOTS:
            break
    if not slots:
        return _fallback_decision(candidate_slots)
    return OrchestratorDecision(slots=slots, rationale=decision.rationale)
