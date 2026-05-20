"""v2-first read service for Trace Monitor APIs.

The service keeps the public API shape stable while preferring normalized v2
tables. Legacy ``traces`` remains the fallback source during migration.
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from db import Observation, Score, Trace, TraceV2
from services.trace_repositories import observation_to_trace_payload


QUALITY_DIMENSIONS = [
    "grounding",
    "task_fit",
    "completeness",
    "specificity",
    "source_quality",
    "uncertainty_honesty",
    "format_fit",
]


def _agent_execution_root():
    router_observation_ids = (
        select(Trace.observation_id).where(Trace.agent_name == "router_agent").scalar_subquery()
    )
    return or_(
        Trace.trace_id.in_(router_observation_ids),
        and_(Trace.trace_id.is_(None), Trace.agent_name != "router_agent"),
    )


def _parse_json(value):
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return value


def _json_text(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def _truncate(value, max_len: int = 200) -> str | None:
    if value is None:
        return None
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return text[:max_len] + "..." if len(text) > max_len else text


def _latency(start, end) -> float | None:
    if start and end:
        return round((end - start).total_seconds(), 2)
    return None


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value if value and value != "all" else None


def _metadata(trace: TraceV2) -> dict:
    data = _parse_json(trace.metadata_json) or {}
    return data if isinstance(data, dict) else {}


def _score_summary_from_rows(rows: list[Score]) -> tuple[float | None, dict | None, str | None]:
    if not rows:
        return None, None, None
    numeric: dict[str, float] = {}
    feedback: str | None = None
    verdict: str | None = None
    for row in rows:
        if row.value is not None:
            numeric[row.name] = float(row.value)
        if row.comment:
            feedback = row.comment
        if row.string_value and row.name in {"verdict", "feedback"}:
            verdict = row.string_value
    quality_score = numeric.get("overall")
    if quality_score is None:
        # Only average recognised quality dimensions, not operational metrics
        quality_nums = [v for k, v in numeric.items() if k in QUALITY_DIMENSIONS]
        if quality_nums:
            quality_score = round(sum(quality_nums) / len(quality_nums), 2)
    detail = dict(numeric) if numeric else None
    if detail is not None and verdict:
        detail["verdict"] = verdict
    return quality_score, detail, feedback


def _score_summary(db: Session, trace_id: str) -> tuple[float | None, dict | None, str | None]:
    return _score_summary_from_rows(db.query(Score).filter(Score.trace_id == trace_id).all())


def _usage_totals_from_rows(observations: list[Observation]) -> tuple[int, int]:
    prompt_tokens = 0
    completion_tokens = 0
    for obs in observations:
        prompt_tokens += obs.prompt_tokens or 0
        completion_tokens += obs.completion_tokens or 0
    return prompt_tokens, completion_tokens


def _cost_total_from_rows(observations: list[Observation]) -> float | None:
    total = sum(obs.total_cost for obs in observations if obs.total_cost is not None)
    return round(total, 8) if total else None


def _level_info(observations: list[Observation]) -> tuple[str | None, dict | None]:
    """Return (worst_level, non-default level counts)."""
    if not observations:
        return None, None
    counts: dict[str, int] = {}
    for obs in observations:
        lvl = (obs.level or "DEFAULT").upper()
        if lvl != "DEFAULT":
            counts[lvl] = counts.get(lvl, 0) + 1
    if "ERROR" in counts:
        return "ERROR", counts
    if "WARNING" in counts:
        return "WARNING", counts
    return "DEFAULT", None


_STANDARD_META_KEYS = {
    "task_type", "route_intent", "agent_name", "prompt_name", "prompt_version",
    "base_prompt_name", "task_prompt_name", "quality_prompt_name",
    "base_prompt_hash", "task_prompt_hash", "quality_prompt_hash",
    "prompt_stack_name", "prompt_stack_json", "primary_prompt_json",
    "workflow_prompts_json", "prompt_stack_tokens", "tool_count", "llm_call_count",
    "original_intent", "resolved_intent", "document_ids", "display",
}


def _usage_totals(db: Session, trace_id: str) -> tuple[int, int]:
    return _usage_totals_from_rows(
        db.query(Observation).filter(Observation.trace_id == trace_id).all()
    )


def _build_v2_payload(
    trace: TraceV2,
    scores: list[Score],
    observations: list[Observation],
    *,
    include_raw: bool = False,
    include_display: bool = True,
) -> dict:
    meta = _metadata(trace)
    quality_score, quality_detail, user_feedback = _score_summary_from_rows(scores)
    input_obj = _parse_json(trace.input)
    output_obj = _parse_json(trace.output)
    prompt_tokens, completion_tokens = _usage_totals_from_rows(observations)
    total_cost = _cost_total_from_rows(observations)
    input_cost = round(sum(o.input_cost or 0 for o in observations if o.input_cost is not None), 8) or None
    output_cost = round(sum(o.output_cost or 0 for o in observations if o.output_cost is not None), 8) or None
    tags = _parse_json(trace.tags) if trace.tags else []
    if not isinstance(tags, list):
        tags = []
    worst_level, level_counts = _level_info(observations)
    error = next((o.status_message for o in observations if o.level == "ERROR" and o.status_message), None)
    extra_meta = {k: v for k, v in meta.items() if k not in _STANDARD_META_KEYS} or None

    display = meta.get("display") if isinstance(meta.get("display"), dict) else None
    answer = display.get("answer") if isinstance(display, dict) else None
    if answer is None and isinstance(output_obj, dict):
        answer = output_obj.get("answer") or output_obj.get("content")

    # Extract plain text from input: prefer messages[0].content, fall back to stringify
    input_text: str | None = None
    if isinstance(input_obj, dict):
        messages = input_obj.get("messages") or []
        if messages and isinstance(messages[0], dict):
            input_text = _truncate(messages[0].get("content"))
    input_text = input_text or _truncate(input_obj)

    payload = {
        "id": trace.trace_id,
        "name": trace.name,
        "status": "error" if error else "ok",
        "level": worst_level or "DEFAULT",
        "start_time": trace.start_time.isoformat() + "Z" if trace.start_time else None,
        "end_time": trace.end_time.isoformat() + "Z" if trace.end_time else None,
        "latency": _latency(trace.start_time, trace.end_time),
        "error": error,
        "user_id": trace.user_id,
        "input": input_text,
        "output": _truncate(answer if answer is not None else output_obj),
        "url": None,
        "display": display if include_display else None,
        "task_type": meta.get("task_type"),
        "route_intent": meta.get("route_intent"),
        "agent_name": meta.get("agent_name"),
        "prompt_name": meta.get("prompt_name"),
        "prompt_version": meta.get("prompt_version"),
        "base_prompt_name": meta.get("base_prompt_name"),
        "task_prompt_name": meta.get("task_prompt_name"),
        "quality_prompt_name": meta.get("quality_prompt_name"),
        "base_prompt_hash": meta.get("base_prompt_hash"),
        "task_prompt_hash": meta.get("task_prompt_hash"),
        "quality_prompt_hash": meta.get("quality_prompt_hash"),
        "prompt_stack_name": meta.get("prompt_stack_name"),
        "prompt_stack_json": meta.get("prompt_stack_json"),
        "primary_prompt_json": meta.get("primary_prompt_json"),
        "workflow_prompts_json": meta.get("workflow_prompts_json"),
        "prompt_stack_tokens": meta.get("prompt_stack_tokens"),
        "tool_count": meta.get("tool_count"),
        "llm_call_count": meta.get("llm_call_count"),
        "quality_score": quality_score,
        "user_feedback": user_feedback,
        "original_intent": meta.get("original_intent"),
        "resolved_intent": meta.get("resolved_intent"),
        "quality_detail": quality_detail,
        "runtime_prompt_metadata": {},
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_cost": total_cost,
        "input_cost": input_cost,
        "output_cost": output_cost,
        "tags": tags,
        "thread_id": trace.thread_id,
        "environment": trace.environment,
        "observation_count": len(observations),
        "level": worst_level,
        "obs_level_counts": level_counts,
        "metadata": extra_meta,
    }
    if include_raw:
        payload.update({
            "run_type": "chain",
            "trace_id": None,
            "document_ids": meta.get("document_ids"),
            "inputs_raw": input_obj,
            "outputs_raw": output_obj,
        })
    return payload


def _v2_trace_payload(db: Session, trace: TraceV2, *, include_raw: bool = False, include_display: bool = True) -> dict:
    scores = db.query(Score).filter(Score.trace_id == trace.trace_id).all()
    observations = db.query(Observation).filter(Observation.trace_id == trace.trace_id).all()
    return _build_v2_payload(trace, scores, observations, include_raw=include_raw, include_display=include_display)


def _legacy_latency(t: Trace) -> float | None:
    return _latency(t.start_time, t.end_time)


def _legacy_parse_quality(value) -> dict | None:
    parsed = _parse_json(value)
    return parsed if isinstance(parsed, dict) else None


def legacy_trace_payload(t: Trace, *, include_raw: bool = False, include_display: bool = True) -> dict:
    display = _parse_json(t.display) if t.display else None
    if not isinstance(display, dict):
        display = None
    input_text = None
    if t.inputs:
        inputs = _parse_json(t.inputs)
        if isinstance(inputs, dict):
            messages = inputs.get("messages") or []
            if messages and isinstance(messages[0], dict):
                input_text = _truncate(messages[0].get("content"))
        input_text = input_text or _truncate(inputs)
    output_text = _truncate(display.get("answer")) if isinstance(display, dict) else None
    payload = {
        "id": t.observation_id,
        "name": t.name,
        "status": "error" if t.error else "ok",
        "level": "ERROR" if t.error else "DEFAULT",
        "start_time": t.start_time.isoformat() + "Z" if t.start_time else None,
        "end_time": t.end_time.isoformat() + "Z" if t.end_time else None,
        "latency": _legacy_latency(t),
        "error": t.error,
        "user_id": t.user_id,
        "input": input_text,
        "output": output_text,
        "url": None,
        "display": display if include_display else None,
        "task_type": t.task_type,
        "route_intent": t.route_intent,
        "agent_name": t.agent_name,
        "prompt_name": t.prompt_name,
        "prompt_version": t.prompt_version,
        "base_prompt_name": t.base_prompt_name,
        "task_prompt_name": t.task_prompt_name,
        "quality_prompt_name": t.quality_prompt_name,
        "base_prompt_hash": t.base_prompt_hash,
        "task_prompt_hash": t.task_prompt_hash,
        "quality_prompt_hash": t.quality_prompt_hash,
        "prompt_stack_name": t.prompt_stack_name,
        "prompt_stack_json": _parse_json(t.prompt_stack_json),
        "primary_prompt_json": _parse_json(t.primary_prompt_json),
        "workflow_prompts_json": _parse_json(t.workflow_prompts_json),
        "prompt_stack_tokens": t.prompt_stack_tokens,
        "tool_count": t.tool_count,
        "llm_call_count": t.llm_call_count,
        "quality_score": t.quality_score,
        "user_feedback": t.user_feedback,
        "original_intent": t.original_intent,
        "resolved_intent": t.resolved_intent,
        "quality_detail": _legacy_parse_quality(t.quality_detail),
        "runtime_prompt_metadata": display.get("prompt_metadata", {}) if isinstance(display, dict) else {},
        "prompt_tokens": t.prompt_tokens,
        "completion_tokens": t.completion_tokens,
        "total_cost": None,
        "input_cost": None,
        "output_cost": None,
        "tags": None,
        "thread_id": t.thread_id,
        "environment": t.environment,
        "observation_count": None,
        "level": "ERROR" if t.error else "DEFAULT",
        "obs_level_counts": {"ERROR": 1} if t.error else None,
        "metadata": None,
    }
    if include_raw:
        payload.update({
            "run_type": t.run_type,
            "trace_id": t.trace_id,
            "document_ids": _parse_json(t.document_ids),
            "inputs_raw": _parse_json(t.inputs),
            "outputs_raw": _parse_json(t.outputs),
        })
    return payload


__all__ = [name for name in globals() if not name.startswith("__")]


