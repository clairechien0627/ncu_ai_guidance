"""General-purpose output and trace evaluation agent.

This module is the broader evaluator used by trace monitoring and, when
requested, by router_agent. Extraction Pipeline Step 4 lives separately in
``services.extraction_quality`` and is powered by ``summary_quality``.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import AzureChatOpenAI
from langfuse import observe
from pydantic import BaseModel, Field

from config import settings
from db import db_session
from observability import ainvoke_traced_generation
from prompting.loader import load_stack

logger = logging.getLogger(__name__)

PROMPT_NAME = "evaluation_agent"
AGENT_NAME = "evaluation"
STACK_NAME = "evaluation_default"


class EvaluationResult(BaseModel):
    grounding: float = Field(ge=0, le=5, description="Evidence support score.")
    task_fit: float = Field(ge=0, le=5, description="How well the answer addresses the task.")
    completeness: float = Field(ge=0, le=5, description="Coverage of required task aspects.")
    specificity: float = Field(ge=0, le=5, description="Concrete detail and useful specificity.")
    source_quality: float = Field(ge=0, le=5, description="Topical coherence between claims and cited sources.")
    uncertainty_honesty: float = Field(ge=0, le=5, description="Honesty about missing evidence or uncertainty.")
    format_fit: float = Field(ge=0, le=5, description="Fit to requested format, language, and audience.")
    overall: float = Field(default=0.0, ge=0, le=5, description="Weighted overall score.")
    dimension_reasoning: dict[str, str] = Field(
        default_factory=dict,
        description="One-sentence reasoning per dimension before scoring.",
    )
    verdict: str = Field(description="Short verdict in Traditional Chinese.")
    failure_modes: list[str] = Field(
        default_factory=list,
        description="Detected failure modes from taxonomy, max 5.",
    )
    suggested_fixes: list[str] = Field(default_factory=list, description="Actionable fixes, max 3.")


WEIGHT_PROFILES: dict[str, dict[str, float]] = {
    "chat": {
        "grounding": 0.15, "task_fit": 0.25, "completeness": 0.15,
        "specificity": 0.12, "source_quality": 0.08, "uncertainty_honesty": 0.15, "format_fit": 0.10,
    },
    "retrieval": {
        "grounding": 0.30, "task_fit": 0.20, "completeness": 0.15,
        "specificity": 0.12, "source_quality": 0.10, "uncertainty_honesty": 0.08, "format_fit": 0.05,
    },
    "research": {
        "grounding": 0.25, "task_fit": 0.18, "completeness": 0.22,
        "specificity": 0.12, "source_quality": 0.10, "uncertainty_honesty": 0.08, "format_fit": 0.05,
    },
    "question": {
        "grounding": 0.10, "task_fit": 0.25, "completeness": 0.20,
        "specificity": 0.15, "source_quality": 0.08, "uncertainty_honesty": 0.07, "format_fit": 0.15,
    },
    "document_extraction": {
        "grounding": 0.20, "task_fit": 0.20, "completeness": 0.25,
        "specificity": 0.15, "source_quality": 0.10, "uncertainty_honesty": 0.05, "format_fit": 0.05,
    },
}

DEFAULT_WEIGHTS: dict[str, float] = {
    "grounding": 0.25, "task_fit": 0.18, "completeness": 0.17,
    "specificity": 0.12, "source_quality": 0.10, "uncertainty_honesty": 0.10, "format_fit": 0.08,
}


def _llm():
    return AzureChatOpenAI(
        azure_deployment=settings.azure_mini_deployment or settings.azure_chat_deployment,
        azure_endpoint=settings.azure_openai_endpoint,
        api_key=settings.azure_openai_api_key.get_secret_value(),
        api_version=settings.azure_openai_api_version,
        temperature=0,
    )


def _weighted_overall(result: EvaluationResult, agent_name: str = "chat") -> float:
    weights = WEIGHT_PROFILES.get(agent_name, DEFAULT_WEIGHTS)
    raw = sum(getattr(result, dim) * w for dim, w in weights.items())
    return max(0.0, min(5.0, round(raw, 2)))


def _json_loads(value: str | dict | list | None) -> Any:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return None


def _completion_from_coverage(trace_summary: dict) -> float | None:
    coverage = trace_summary.get("coverage") or []
    required = [item for item in coverage if item.get("required")]
    if not required:
        return None
    filled = sum(1 for item in required if item.get("status") in ("FILLED", "PARTIAL"))
    return round(filled / len(required) * 5, 2)


@observe(as_type="agent", name="Evaluate Output")
async def evaluate_output(
    *,
    user_task: str,
    answer: str,
    agent_name: str = "chat",
    sources: list[str] | None = None,
    trace_summary: dict | None = None,
    extra_context: dict | None = None,
) -> EvaluationResult:
    """Evaluate an answer without mutating application state."""
    trace_summary = trace_summary or {}
    sources = sources or []
    answer_limit = 8000 if agent_name == "research" else 5000
    payload = {
        "user_task": user_task,
        "answer": answer[:answer_limit],
        "agent_name": agent_name,
        "sources": sources[:12],
        "sources_empty": not sources,
        "trace_summary": trace_summary,
        "extra_context": extra_context or {},
        "response_contract": {
            "failure_modes_max": 5,
            "suggested_fixes_max": 3,
        },
    }
    stack = load_stack(STACK_NAME)
    system_messages = [
        SystemMessage(content=content)
        for content in stack.contents
    ]
    scorer = _llm().with_structured_output(EvaluationResult)
    result: EvaluationResult = await ainvoke_traced_generation(
        scorer,
        [
            *system_messages,
            HumanMessage(content=json.dumps(payload, ensure_ascii=False)),
        ],
        prompt_name=PROMPT_NAME,
        metadata={
            "agent_name": AGENT_NAME,
            **stack.metadata(),
        },
    )
    # source_quality override: if no sources were provided, the LLM cannot
    # evaluate citation coherence — set to 0 deterministically.
    if not sources:
        result = result.model_copy(update={"source_quality": 0.0})
    return result.model_copy(update={"overall": _weighted_overall(result, agent_name)})


async def evaluate_trace_by_observation_id(observation_id: str) -> EvaluationResult | None:
    """Evaluate a Trace v2 root and persist scores."""
    try:
        from services.evaluation.runs import EvaluationRunService
        result = await EvaluationRunService.evaluate_single_trace(
            observation_id,
            evaluator=evaluate_output,
            name="background-evaluation",
            scope="single_trace",
            metadata={"source": "evaluate_trace_by_observation_id"},
        )
        return result if isinstance(result, EvaluationResult) else None
    except Exception as exc:
        logger.warning("evaluate_trace_by_observation_id failed for %s: %s", observation_id, exc)
        return None


async def evaluate_direct_answer(
    *,
    user_task: str,
    answer: str,
    sources: list[str],
    agent_name: str = "chat",
) -> EvaluationResult | None:
    """Evaluate a provided answer directly without searching the DB for a matching trace.

    Used by the evaluate_after background path so that multi-agent tasks (e.g.
    question_agent with evidence collection, or escalation chains) are always
    evaluated against the actual final response shown to the user, not whatever
    happens to be the most-recent trace row in the DB.
    """
    try:
        return await evaluate_output(
            user_task=user_task,
            answer=answer,
            agent_name=agent_name,
            sources=sources,
        )
    except Exception as exc:
        logger.warning("evaluate_direct_answer failed: %s", exc)
        return None


async def evaluate_latest_thread_message(
    thread_id: str,
    *,
    exclude_observation_id: str | None = None,
) -> EvaluationResult | None:
    """Evaluate the latest AgentMessage for a thread without reading trace tables."""
    def _find():
        with db_session() as db:
            from db.models import AgentMessage
            q = db.query(AgentMessage).filter(
                AgentMessage.thread_id == thread_id,
                AgentMessage.agent_answer.isnot(None),
            )
            if exclude_observation_id:
                q = q.filter(AgentMessage.observation_id != exclude_observation_id)
            return q.order_by(AgentMessage.created_at.desc()).first()

    msg = await asyncio.to_thread(_find)
    if not msg:
        return None
    return await evaluate_output(
        user_task=msg.user_question or "",
        answer=msg.agent_answer or "",
        sources=msg.sources or [],
        trace_summary=msg.trace_summary or {},
        agent_name=msg.agent_name,
    )


def format_evaluation_for_user(result: EvaluationResult | None) -> str:
    if result is None:
        return "找不到可評估的最近一次輸出。"
    lines = [
        f"評估結果：{result.verdict}",
        f"總分：{result.overall:.1f}/5",
        f"證據支撐：{result.grounding:.1f}，任務符合：{result.task_fit:.1f}，完整度：{result.completeness:.1f}",
    ]
    if result.failure_modes:
        lines.append("問題類型：" + "、".join(result.failure_modes[:3]))
    if result.suggested_fixes:
        lines.append("建議修正：" + "；".join(result.suggested_fixes[:3]))
    return "\n".join(lines)
