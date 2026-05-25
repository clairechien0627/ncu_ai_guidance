"""Trace v2 read helpers for Trace Monitor APIs."""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from db import Observation, Score, TraceV2
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
        if row.name == "feedback" and row.string_value:
            feedback = row.string_value
        elif row.comment and feedback is None:
            feedback = row.comment
        if row.string_value and row.name == "verdict":
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


def _status_info(observations: list[Observation]) -> tuple[str | None, dict | None]:
    """Return (worst_status, non-default status counts)."""
    if not observations:
        return None, None
    counts: dict[str, int] = {}
    for obs in observations:
        status = (obs.status or "DEFAULT").upper()
        if status != "DEFAULT":
            counts[status] = counts.get(status, 0) + 1
    if "ERROR" in counts:
        return "ERROR", counts
    if "WARNING" in counts:
        return "WARNING", counts
    if "DEBUG" in counts:
        return "DEBUG", counts
    return "DEFAULT", None


_STANDARD_META_KEYS = {
    "agent_name", "prompt_name", "prompt_version", "prompt_id",
    "base_prompt_name", "task_prompt_name", "quality_prompt_name",
    "base_prompt_hash", "task_prompt_hash", "quality_prompt_hash",
    "prompt_stack_name", "prompt_stack_json", "primary_prompt_json",
    "workflow_prompts_json", "prompt_stack_tokens",
    "research_effective_base_prompt_stack_json", "research_effective_system_prompt_json",
    "research_runtime_prompt_json", "research_runtime_prompt_summary",
    "extract_step2_prompt_stack_json", "extract_step3_prompt_stack_json",
    "extract_step4_prompt_stack_json", "tool_count", "llm_call_count",
    "document_ids", "display",
}


def _usage_totals(db: Session, trace_id: str) -> tuple[int, int]:
    return _usage_totals_from_rows(
        db.query(Observation).filter(Observation.trace_id == trace_id).all()
    )


def _observation_counts(observations: list[Observation]) -> tuple[int, int]:
    tool_count = 0
    llm_call_count = 0
    for obs in observations:
        obs_type = (obs.type or "").upper()
        if obs_type == "TOOL":
            tool_count += 1
        elif obs_type == "GENERATION":
            llm_call_count += 1
    return tool_count, llm_call_count


def _build_v2_payload(
    trace: TraceV2,
    scores: list[Score],
    observations: list[Observation],
    *,
    include_raw: bool = False,
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
    worst_status, status_counts = _status_info(observations)
    error = next((o.status_message for o in observations if o.status == "ERROR" and o.status_message), None)
    extra_meta = {k: v for k, v in meta.items() if k not in _STANDARD_META_KEYS} or None
    tool_count, llm_call_count = _observation_counts(observations)

    answer = None
    if isinstance(output_obj, dict):
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
        "status": worst_status or "DEFAULT",
        "start_time": trace.start_time.isoformat() + "Z" if trace.start_time else None,
        "end_time": trace.end_time.isoformat() + "Z" if trace.end_time else None,
        "latency": _latency(trace.start_time, trace.end_time),
        "error": error,
        "user_id": trace.user_id,
        "bookmarked": bool(getattr(trace, "bookmarked", False)),
        "input": input_text,
        "output": _truncate(answer if answer is not None else output_obj),
        "url": None,
        "agent_name": meta.get("agent_name"),
        "tool_count": tool_count,
        "llm_call_count": llm_call_count,
        "quality_score": quality_score,
        "user_feedback": user_feedback,
        "quality_detail": quality_detail,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
        "total_cost": total_cost,
        "input_cost": input_cost,
        "output_cost": output_cost,
        "tags": tags,
        "thread_id": trace.thread_id,
        "environment": trace.environment,
        "observation_count": len(observations),
        "obs_status_counts": status_counts,
        "metadata": extra_meta,
    }
    if include_raw:
        payload.update({
            "trace_id": None,
            "document_ids": meta.get("document_ids"),
            "inputs_raw": input_obj,
            "outputs_raw": output_obj,
        })
    return payload


def _v2_trace_payload(db: Session, trace: TraceV2, *, include_raw: bool = False) -> dict:
    scores = db.query(Score).filter(Score.trace_id == trace.trace_id).all()
    observations = db.query(Observation).filter(Observation.trace_id == trace.trace_id).all()
    return _build_v2_payload(trace, scores, observations, include_raw=include_raw)


__all__ = [name for name in globals() if not name.startswith("__")]


