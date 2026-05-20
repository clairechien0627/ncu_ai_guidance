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
from db import db_session, Trace
from observability import ainvoke_traced_generation
from prompting.loader import load_stack

logger = logging.getLogger(__name__)

PROMPT_NAME = "evaluation_agent"
AGENT_NAME = "evaluation_agent"
STACK_NAME = "evaluation_default"


class EvaluationResult(BaseModel):
    grounding: float = Field(ge=0, le=5, description="Evidence support score.")
    task_fit: float = Field(ge=0, le=5, description="How well the answer addresses the task.")
    completeness: float = Field(ge=0, le=5, description="Coverage of required task aspects.")
    specificity: float = Field(ge=0, le=5, description="Concrete detail and useful specificity.")
    source_quality: float = Field(ge=0, le=5, description="Quality and relevance of cited sources.")
    uncertainty_honesty: float = Field(ge=0, le=5, description="Honesty about missing evidence or uncertainty.")
    format_fit: float = Field(ge=0, le=5, description="Fit to requested format, language, and audience.")
    overall: float = Field(default=0.0, ge=0, le=5, description="Weighted overall score.")
    verdict: str = Field(description="Short verdict in Traditional Chinese.")
    issues: list[str] = Field(default_factory=list, description="Concrete problems, max 5.")
    evidence_gaps: list[str] = Field(default_factory=list, description="Missing or weak evidence, max 5.")
    suggested_fixes: list[str] = Field(default_factory=list, description="Actionable fixes, max 5.")
    should_rerun_retrieval: bool = False
    should_rerun_research: bool = False


def _llm():
    return AzureChatOpenAI(
        azure_deployment=settings.azure_mini_deployment or settings.azure_chat_deployment,
        azure_endpoint=settings.azure_openai_endpoint,
        api_key=settings.azure_openai_api_key.get_secret_value(),
        api_version=settings.azure_openai_api_version,
        temperature=0,
    )


def _weighted_overall(result: EvaluationResult) -> float:
    raw = (
        result.grounding * 0.25
        + result.task_fit * 0.18
        + result.completeness * 0.17
        + result.specificity * 0.12
        + result.source_quality * 0.10
        + result.uncertainty_honesty * 0.10
        + result.format_fit * 0.08
    )
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
    task_type: str = "chat_turn",
    sources: list[str] | None = None,
    trace_summary: dict | None = None,
    extra_context: dict | None = None,
) -> EvaluationResult:
    """Evaluate an answer without mutating application state."""
    trace_summary = trace_summary or {}
    sources = sources or []
    payload = {
        "user_task": user_task,
        "answer": answer[:5000],
        "task_type": task_type,
        "sources": sources[:12],
        "trace_summary": trace_summary,
        "extra_context": extra_context or {},
        "response_contract": {
            "issues_max": 5,
            "evidence_gaps_max": 5,
            "suggested_fixes_max": 5,
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
            "task_type": task_type,
            "agent_name": AGENT_NAME,
            **stack.metadata(),
        },
    )
    coverage_score = _completion_from_coverage(trace_summary)
    if coverage_score is not None:
        result = result.model_copy(update={"completeness": coverage_score})
    return result.model_copy(update={"overall": _weighted_overall(result)})


async def score_trace(
    display: dict,
    task_type: str = "chat_turn",
) -> tuple[float, str, dict]:
    """Compatibility scoring API for trace monitor batch scoring."""
    answer = str(display.get("answer") or "")
    trace_summary = display.get("trace_summary") if isinstance(display.get("trace_summary"), dict) else {}
    sources = display.get("sources") if isinstance(display.get("sources"), list) else []
    user_task = ""
    messages = display.get("messages") if isinstance(display.get("messages"), list) else []
    for msg in messages:
        if msg.get("role") in ("human", "user"):
            user_task = str(msg.get("content") or "")
            break
    try:
        result = await evaluate_output(
            user_task=user_task,
            answer=answer,
            task_type=task_type,
            sources=sources,
            trace_summary=trace_summary,
        )
        detail = result.model_dump()
        explanation = _format_evaluation(result)
        return result.overall, explanation, detail
    except Exception as exc:
        logger.warning("score_trace failed: %s", exc)
        return 0.0, f"score_trace_failed: {exc}", {}


def _format_evaluation(result: EvaluationResult) -> str:
    issues = "；".join(result.issues[:3]) if result.issues else "未列出明顯問題"
    return (
        f"overall {result.overall:.1f} | grounding {result.grounding:.1f} / "
        f"task_fit {result.task_fit:.1f} / completeness {result.completeness:.1f} | "
        f"{result.verdict} | {issues}"
    )


def _trace_payload(trace: Trace) -> tuple[str, str, list[str], dict]:
    display = _json_loads(trace.display) or {}
    outputs = _json_loads(trace.outputs) or {}
    inputs = _json_loads(trace.inputs) or {}
    answer = str(display.get("answer") or outputs.get("answer") or "")
    sources = display.get("sources") if isinstance(display.get("sources"), list) else []
    trace_summary = display.get("trace_summary") if isinstance(display.get("trace_summary"), dict) else {}
    user_task = ""
    messages = display.get("messages") if isinstance(display.get("messages"), list) else []
    for msg in messages:
        if msg.get("role") in ("human", "user"):
            user_task = str(msg.get("content") or "")
            break
    if not user_task:
        user_task = json.dumps(inputs, ensure_ascii=False)[:1200] if inputs else ""
    return user_task, answer, sources, trace_summary


async def evaluate_trace_by_observation_id(observation_id: str) -> EvaluationResult | None:
    """Evaluate a root trace and persist score/detail back to the Trace row."""
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


async def evaluate_latest_trace_for_thread(thread_id: str, *, exclude_observation_id: str | None = None) -> EvaluationResult | None:
    """Evaluate the latest completed task-agent trace for a conversation thread."""
    def _find():
        from sqlalchemy import or_, and_, select
        with db_session() as db:
            router_observation_ids = select(Trace.observation_id).where(Trace.agent_name == "router_agent").scalar_subquery()
            agent_execution_root = or_(
                Trace.trace_id.in_(router_observation_ids),
                and_(Trace.trace_id.is_(None), Trace.agent_name != "router_agent"),
            )
            query = db.query(Trace).filter(
                agent_execution_root,
                Trace.thread_id == thread_id,
                Trace.error.is_(None),
            )
            if exclude_observation_id:
                query = query.filter(Trace.observation_id != exclude_observation_id)
            trace = query.order_by(Trace.start_time.desc()).first()
            return trace.observation_id if trace else None

    observation_id = await asyncio.to_thread(_find)
    if not observation_id:
        return None
    return await evaluate_trace_by_observation_id(observation_id)


def format_evaluation_for_user(result: EvaluationResult | None) -> str:
    if result is None:
        return "找不到可評估的最近一次輸出。"
    lines = [
        f"評估結果：{result.verdict}",
        f"總分：{result.overall:.1f}/5",
        f"證據支撐：{result.grounding:.1f}，任務符合：{result.task_fit:.1f}，完整度：{result.completeness:.1f}",
    ]
    if result.issues:
        lines.append("主要問題：" + "；".join(result.issues[:3]))
    if result.evidence_gaps:
        lines.append("證據缺口：" + "；".join(result.evidence_gaps[:3]))
    if result.suggested_fixes:
        lines.append("建議修正：" + "；".join(result.suggested_fixes[:3]))
    if result.should_rerun_retrieval or result.should_rerun_research:
        actions = []
        if result.should_rerun_retrieval:
            actions.append("補做 focused retrieval")
        if result.should_rerun_research:
            actions.append("重跑 research workflow")
        lines.append("後續建議：" + "、".join(actions))
    return "\n".join(lines)
