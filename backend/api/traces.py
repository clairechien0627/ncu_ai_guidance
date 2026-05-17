import asyncio
import json
import logging
from collections import Counter
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, or_, and_, select
from sqlalchemy.orm import Session

from db import get_db, Trace

logger = logging.getLogger(__name__)

router = APIRouter()


def _agent_execution_root():
    """
    SQLAlchemy filter condition that matches each routed agent execution root.

    Supports both old data where the task agent was the DB root
    (parent_run_id=None) and new data where the task agent is a child of
    router_agent.
    """
    router_run_ids = (
        select(Trace.run_id).where(Trace.agent_name == "router_agent").scalar_subquery()
    )
    return or_(
        Trace.parent_run_id.in_(router_run_ids),
        and_(Trace.parent_run_id.is_(None), Trace.agent_name != "router_agent"),
    )


class TraceFeedbackRequest(BaseModel):
    quality_score: float | None = None
    user_feedback: str | None = None


def _truncate(obj, max_len: int = 200) -> str | None:
    if not obj:
        return None
    text = str(obj)
    return text[:max_len] + "..." if len(text) > max_len else text


def _latency_seconds(t: Trace) -> float | None:
    if t.start_time and t.end_time:
        return round((t.end_time - t.start_time).total_seconds(), 2)
    return None


def _trace_meta(t: Trace) -> dict:
    prompt_stack = _parse_json(getattr(t, "prompt_stack_json", None))
    primary_prompt = _parse_json(getattr(t, "primary_prompt_json", None))
    workflow_prompts = _parse_json(getattr(t, "workflow_prompts_json", None))
    return {
        "task_type": getattr(t, "task_type", None),
        "route_intent": getattr(t, "route_intent", None),
        "agent_name": getattr(t, "agent_name", None),
        "prompt_name": getattr(t, "prompt_name", None),
        "prompt_version": getattr(t, "prompt_version", None),
        "base_prompt_name": getattr(t, "base_prompt_name", None),
        "task_prompt_name": getattr(t, "task_prompt_name", None),
        "quality_prompt_name": getattr(t, "quality_prompt_name", None),
        "base_prompt_hash": getattr(t, "base_prompt_hash", None),
        "task_prompt_hash": getattr(t, "task_prompt_hash", None),
        "quality_prompt_hash": getattr(t, "quality_prompt_hash", None),
        "prompt_stack_name": getattr(t, "prompt_stack_name", None),
        "prompt_stack_json": prompt_stack,
        "primary_prompt_json": primary_prompt,
        "workflow_prompts_json": workflow_prompts,
        "prompt_stack_tokens": getattr(t, "prompt_stack_tokens", None),
        "tool_count": getattr(t, "tool_count", None),
        "llm_call_count": getattr(t, "llm_call_count", None),
        "quality_score": getattr(t, "quality_score", None),
        "user_feedback": getattr(t, "user_feedback", None),
        "original_intent": getattr(t, "original_intent", None),
        "resolved_intent": getattr(t, "resolved_intent", None),
        "quality_detail": _parse_quality_detail(getattr(t, "quality_detail", None)),
    }


def _prompt_metadata_from_display(t: Trace) -> dict:
    if not t.display:
        return {}
    try:
        display = json.loads(t.display)
    except Exception:
        return {}
    if not isinstance(display, dict) or not isinstance(display.get("prompt_metadata"), dict):
        return {}
    return display["prompt_metadata"]


def _display(t: Trace) -> dict | None:
    if not t.display:
        return None
    try:
        display = json.loads(t.display)
    except Exception:
        return None
    if isinstance(display, dict) and isinstance(display.get("messages"), list):
        display["messages"] = _normalize_trace_messages(display["messages"])
    return display


def _normalize_trace_messages(messages: list[dict]) -> list[dict]:
    normalized: list[dict] = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        kind = msg.get("role") or msg.get("type")
        content = str(msg.get("content") or "")

        if kind in ("system", "human"):
            normalized.append({"role": kind, "content": content})
        elif kind == "ai":
            if content:
                normalized.append({"role": "ai", "content": content})
            tool_calls = msg.get("tool_calls") or []
            if tool_calls:
                normalized.append({
                    "role": "ai_tool_call",
                    "tool_calls": [
                        {
                            "tool": call.get("tool") or call.get("name") or "tool",
                            "call_id": call.get("call_id") or call.get("id") or "",
                            "args": call.get("args") or {},
                        }
                        for call in tool_calls
                        if isinstance(call, dict)
                    ],
                })
        elif kind == "ai_tool_call":
            normalized.append({"role": "ai_tool_call", "tool_calls": msg.get("tool_calls") or []})
        elif kind == "tool":
            raw = msg.get("raw") or content
            chunks = msg.get("chunks") if isinstance(msg.get("chunks"), list) else []
            if not chunks and raw:
                try:
                    payload = json.loads(raw)
                    chunks = [
                        {
                            "filename": chunk.get("filename", ""),
                            "page": chunk.get("page"),
                            "content": str(chunk.get("content", ""))[:900],
                        }
                        for chunk in payload.get("results", [])[:4]
                    ]
                except Exception:
                    chunks = []
            normalized.append({
                "role": "tool",
                "tool": msg.get("tool") or msg.get("name") or "tool",
                "call_id": msg.get("call_id") or msg.get("tool_call_id") or "",
                "chunks": chunks,
                "raw": raw,
            })
    return normalized


def _parse_json(text: str | None):
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        return text


def _parse_quality_detail(value) -> dict | None:
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    try:
        return json.loads(value)
    except Exception:
        return None


def _trace_payload(t: Trace, *, include_raw: bool = False) -> dict:
    payload = {
        "id": t.run_id,
        "name": t.name,
        "status": "error" if t.error else "success",
        "start_time": t.start_time.isoformat() + "Z" if t.start_time else None,
        "end_time": t.end_time.isoformat() + "Z" if t.end_time else None,
        "latency": _latency_seconds(t),
        "error": t.error,
        "input": None,
        "output": None,
        "url": None,
        "display": _display(t),
        **_trace_meta(t),
        "runtime_prompt_metadata": _prompt_metadata_from_display(t),
    }
    if t.inputs:
        try:
            inp = json.loads(t.inputs)
            msgs = inp.get("messages", []) if isinstance(inp, dict) else []
            if msgs:
                payload["input"] = _truncate(msgs[0].get("content", ""))
        except Exception:
            payload["input"] = _truncate(t.inputs)
    display = payload.get("display")
    if isinstance(display, dict):
        payload["output"] = _truncate(display.get("answer", ""))
    if include_raw:
        payload.update({
            "run_type": t.run_type,
            "parent_run_id": t.parent_run_id,
            "thread_id": t.thread_id,
            "document_ids": _parse_json(t.document_ids),
            "inputs_raw": _parse_json(t.inputs),
            "outputs_raw": _parse_json(t.outputs),
            "prompt_tokens": t.prompt_tokens,
            "completion_tokens": t.completion_tokens,
        })
    return payload


def _clean_filter(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value if value and value != "all" else None


def _matches_trace_filters(
    t: Trace,
    *,
    task_type: str | None = None,
    route_intent: str | None = None,
    prompt_name: str | None = None,
    prompt_version: str | None = None,
    status: str | None = None,
    min_latency: float | None = None,
) -> bool:
    task_type = _clean_filter(task_type)
    route_intent = _clean_filter(route_intent)
    prompt_name = _clean_filter(prompt_name)
    prompt_version = _clean_filter(prompt_version)
    status = _clean_filter(status)

    if task_type and (t.task_type or "unknown") != task_type:
        return False
    if route_intent and (t.route_intent or "unknown") != route_intent:
        return False
    if prompt_name and (t.prompt_name or "unknown") != prompt_name:
        return False
    if prompt_version and (t.prompt_version or "unknown") != prompt_version:
        return False
    if status == "success" and t.error:
        return False
    if status == "error" and not t.error:
        return False
    if min_latency is not None and (_latency_seconds(t) or 0) < min_latency:
        return False
    return True


@router.get("/api/documents/{doc_id}/traces")
def get_document_traces(doc_id: int, db: Session = Depends(get_db)):
    # document_ids is jsonb (migration 009). Use @> containment with GIN index.
    from sqlalchemy import func
    doc_filter = Trace.document_ids.op('@>')(func.jsonb_build_array(doc_id))
    candidates = (
        db.query(Trace)
        .filter(_agent_execution_root(), Trace.document_ids.isnot(None), doc_filter)
        .order_by(Trace.start_time.desc())
        .limit(200)
        .all()
    )
    extract_traces = []
    other_traces = []
    for t in candidates:
        if t.thread_id and t.thread_id.startswith("_extract_"):
            extract_traces.append(t)
        elif t.thread_id and not t.thread_id.lstrip("-").isdigit():
            other_traces.append(t)

    seen = {t.run_id for t in extract_traces}
    combined = extract_traces + [t for t in other_traces if t.run_id not in seen]

    return [
        {
            "id": t.run_id,
            "name": t.name,
            "status": "error" if t.error else "success",
            "start_time": t.start_time.isoformat() + "Z" if t.start_time else None,
            "latency": _latency_seconds(t),
            "error": t.error,
            "url": None,
            "display": _display(t),
            **_trace_meta(t),
            "runtime_prompt_metadata": _prompt_metadata_from_display(t),
        }
        for t in combined[:10]
    ]


@router.get("/api/traces/environments")
def list_environments(db: Session = Depends(get_db)):
    """Return distinct environment values present in root traces."""
    rows = (
        db.query(Trace.environment)
        .filter(_agent_execution_root(), Trace.environment.isnot(None))
        .distinct()
        .all()
    )
    return sorted({r[0] for r in rows if r[0]})


@router.get("/api/traces")
def list_traces(
    limit: int = 40,
    offset: int = 0,
    task_type: str | None = None,
    route_intent: str | None = None,
    prompt_name: str | None = None,
    prompt_version: str | None = None,
    status: str | None = None,
    min_latency: float | None = None,
    max_quality: float | None = None,
    has_score: bool | None = None,
    original_intent: str | None = None,
    resolved_intent: str | None = None,
    environment: str | None = None,
    db: Session = Depends(get_db),
):
    q = db.query(Trace).filter(_agent_execution_root())

    task_type = _clean_filter(task_type)
    route_intent = _clean_filter(route_intent)
    prompt_name = _clean_filter(prompt_name)
    prompt_version = _clean_filter(prompt_version)
    status = _clean_filter(status)
    original_intent = _clean_filter(original_intent)
    resolved_intent = _clean_filter(resolved_intent)

    if task_type:
        q = q.filter(Trace.task_type == task_type)
    if route_intent:
        q = q.filter(Trace.route_intent == route_intent)
    if prompt_name:
        q = q.filter(Trace.prompt_name == prompt_name)
    if prompt_version:
        q = q.filter(Trace.prompt_version == prompt_version)
    if status == "error":
        q = q.filter(Trace.error.isnot(None))
    elif status == "success":
        q = q.filter(Trace.error.is_(None))
    if original_intent:
        q = q.filter(Trace.original_intent == original_intent)
    if resolved_intent:
        q = q.filter(Trace.resolved_intent == resolved_intent)
    if environment:
        q = q.filter(Trace.environment == environment)
    if max_quality is not None:
        q = q.filter(Trace.quality_score.isnot(None), Trace.quality_score < max_quality)
    if has_score is True:
        q = q.filter(Trace.quality_score.isnot(None))
    elif has_score is False:
        q = q.filter(Trace.quality_score.is_(None))

    traces = (
        q.order_by(Trace.start_time.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    # min_latency requires computed value; filter in Python only when needed
    if min_latency is not None:
        traces = [t for t in traces if (_latency_seconds(t) or 0) >= min_latency]

    result = []
    for t in traces:
        item = _trace_payload(t)
        item["display"] = None
        result.append(item)
    return result


@router.get("/api/traces/stats")
def trace_stats(days: int = 7, db: Session = Depends(get_db)):
    from datetime import timedelta, timezone as tz
    root = _agent_execution_root()
    now = datetime.now(tz.utc)
    cur_start  = now - timedelta(days=days)
    prev_start = now - timedelta(days=days * 2)

    total_all = db.query(func.count(Trace.id)).filter(root).scalar() or 0

    cur_traces  = db.query(Trace).filter(root, Trace.start_time >= cur_start).all()
    prev_traces = db.query(Trace).filter(
        root, Trace.start_time >= prev_start, Trace.start_time < cur_start,
    ).all()

    def _agg(traces):
        n = len(traces)
        errs = sum(1 for t in traces if t.error)
        lats = [v for v in (_latency_seconds(t) for t in traces) if v is not None]
        qs   = [t.quality_score for t in traces if t.quality_score is not None]
        return {
            "runs":        n,
            "errors":      errs,
            "error_rate":  round(errs / n * 100, 1) if n else 0.0,
            "avg_latency": round(sum(lats) / len(lats), 2) if lats else None,
            "avg_quality": round(sum(qs) / len(qs), 2) if qs else None,
        }

    def _trend(cur_val, prev_val):
        if prev_val is None or prev_val == 0:
            return None
        return round((cur_val - prev_val) / prev_val * 100, 1)

    cur  = _agg(cur_traces)
    prev = _agg(prev_traces)

    return {
        # All-time
        "total_runs": total_all,
        # Current period
        "period_runs":     cur["runs"],
        "error_runs":      cur["errors"],
        "success_runs":    cur["runs"] - cur["errors"],
        "error_rate":      cur["error_rate"],
        "avg_latency":     cur["avg_latency"],
        "avg_quality":     cur["avg_quality"],
        # Trends vs previous period (positive = up, negative = down)
        "runs_trend":      _trend(cur["runs"],        prev["runs"]),
        "error_rate_trend":_trend(cur["error_rate"],  prev["error_rate"]),
        "latency_trend":   _trend(cur["avg_latency"], prev["avg_latency"]),
        "quality_trend":   _trend(cur["avg_quality"], prev["avg_quality"]),
        # Legacy fields kept for AdminTracesPage compatibility
        "avg_tool_count":     0,
        "avg_llm_call_count": 0,
    }


def _grouped_trace_stats(traces: list[Trace], field: str) -> list[dict]:
    groups: dict[str, dict] = {}
    for t in traces:
        key = getattr(t, field) or "unknown"
        item = groups.setdefault(key, {
            "key": key,
            "runs": 0,
            "errors": 0,
            "latencies": [],
            "tokens": 0,
            "quality_scores": [],
            "feedback_count": 0,
        })
        item["runs"] += 1
        if t.error:
            item["errors"] += 1
        latency = _latency_seconds(t)
        if latency is not None:
            item["latencies"].append(latency)
        item["tokens"] += (t.prompt_tokens or 0) + (t.completion_tokens or 0)
        if t.quality_score is not None:
            item["quality_scores"].append(t.quality_score)
        if t.user_feedback:
            item["feedback_count"] += 1

    result = []
    for item in groups.values():
        latencies = item.pop("latencies")
        quality_scores = item.pop("quality_scores")
        item["avg_latency"] = round(sum(latencies) / len(latencies), 2) if latencies else None
        item["avg_quality_score"] = round(sum(quality_scores) / len(quality_scores), 2) if quality_scores else None
        result.append(item)
    return sorted(result, key=lambda x: x["runs"], reverse=True)


def _grouped_prompt_version_stats(traces: list[Trace]) -> list[dict]:
    groups: dict[str, dict] = {}
    for t in traces:
        prompt_name = t.prompt_name or "unknown"
        prompt_version = t.prompt_version or "unknown"
        key = f"{prompt_name}@{prompt_version}"
        item = groups.setdefault(key, {
            "key": key,
            "runs": 0,
            "errors": 0,
            "latencies": [],
            "tokens": 0,
            "quality_scores": [],
            "feedback_count": 0,
        })
        item["runs"] += 1
        if t.error:
            item["errors"] += 1
        latency = _latency_seconds(t)
        if latency is not None:
            item["latencies"].append(latency)
        item["tokens"] += (t.prompt_tokens or 0) + (t.completion_tokens or 0)
        if t.quality_score is not None:
            item["quality_scores"].append(t.quality_score)
        if t.user_feedback:
            item["feedback_count"] += 1

    result = []
    for item in groups.values():
        latencies = item.pop("latencies")
        quality_scores = item.pop("quality_scores")
        item["avg_latency"] = round(sum(latencies) / len(latencies), 2) if latencies else None
        item["avg_quality_score"] = round(sum(quality_scores) / len(quality_scores), 2) if quality_scores else None
        result.append(item)
    return sorted(result, key=lambda x: x["runs"], reverse=True)


@router.get("/api/traces/by-task-type")
def traces_by_task_type(db: Session = Depends(get_db)):
    traces = db.query(Trace).filter(_agent_execution_root()).order_by(Trace.start_time.desc()).limit(1000).all()
    return _grouped_trace_stats(traces, "task_type")


@router.get("/api/traces/by-route-intent")
def traces_by_route_intent(db: Session = Depends(get_db)):
    traces = db.query(Trace).filter(_agent_execution_root()).order_by(Trace.start_time.desc()).limit(1000).all()
    return _grouped_trace_stats(traces, "route_intent")


@router.get("/api/traces/by-prompt")
def traces_by_prompt(db: Session = Depends(get_db)):
    traces = db.query(Trace).filter(_agent_execution_root()).order_by(Trace.start_time.desc()).limit(1000).all()
    return _grouped_trace_stats(traces, "prompt_name")


@router.get("/api/traces/by-prompt-version")
def traces_by_prompt_version(db: Session = Depends(get_db)):
    traces = db.query(Trace).filter(_agent_execution_root()).order_by(Trace.start_time.desc()).limit(1000).all()
    return _grouped_prompt_version_stats(traces)


@router.get("/api/traces/errors")
def trace_errors(limit: int = 40, db: Session = Depends(get_db)):
    traces = (
        db.query(Trace)
        .filter(_agent_execution_root(), Trace.error.isnot(None))
        .order_by(Trace.start_time.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": t.run_id,
            "name": t.name,
            "start_time": t.start_time.isoformat() + "Z" if t.start_time else None,
            "latency": _latency_seconds(t),
            "error": t.error,
            **_trace_meta(t),
        }
        for t in traces
    ]


@router.get("/api/traces/observations")
def list_observations(
    limit: int = 100,
    offset: int = 0,
    run_type: str | None = None,
    db: Session = Depends(get_db),
):
    """Return child spans (LLM calls, tool calls, chains) — i.e. non-root traces."""
    q = db.query(Trace).filter(Trace.parent_run_id.isnot(None))
    if run_type and run_type != "all":
        q = q.filter(Trace.run_type == run_type)
    rows = q.order_by(Trace.start_time.desc()).offset(offset).limit(limit).all()
    return [
        {
            "id":               t.run_id,
            "run_type":         t.run_type,
            "name":             t.name,
            "parent_run_id":    t.parent_run_id,
            "thread_id":        t.thread_id,
            "start_time":       t.start_time.isoformat() + "Z" if t.start_time else None,
            "latency":          _latency_seconds(t),
            "prompt_tokens":    t.prompt_tokens,
            "completion_tokens": t.completion_tokens,
            "error":            t.error,
            "input":            _truncate(t.inputs, 120),
            "output":           _truncate(t.outputs, 120),
        }
        for t in rows
    ]


@router.get("/api/traces/observations/stats")
def observations_stats(db: Session = Depends(get_db)):
    """Aggregate counts and token totals for the Observations page KPI row."""
    from sqlalchemy import case
    rows = (
        db.query(
            Trace.run_type,
            func.count(Trace.id).label("cnt"),
            func.coalesce(func.sum(Trace.prompt_tokens), 0).label("prompt_tok"),
            func.coalesce(func.sum(Trace.completion_tokens), 0).label("completion_tok"),
        )
        .filter(Trace.parent_run_id.isnot(None))
        .group_by(Trace.run_type)
        .all()
    )
    total = sum(r.cnt for r in rows)
    by_type = {r.run_type: {"count": r.cnt, "prompt_tokens": int(r.prompt_tok), "completion_tokens": int(r.completion_tok)} for r in rows}
    total_tokens = sum(int(r.prompt_tok) + int(r.completion_tok) for r in rows)
    return {"total": total, "by_type": by_type, "total_tokens": total_tokens}


@router.get("/api/traces/slow-runs")
def slow_runs(limit: int = 40, min_latency: float = 10, db: Session = Depends(get_db)):
    candidates = (
        db.query(Trace)
        .filter(_agent_execution_root(), Trace.end_time.isnot(None))
        .order_by(Trace.start_time.desc())
        .limit(1000)
        .all()
    )
    traces = [t for t in candidates if (_latency_seconds(t) or 0) >= min_latency]
    traces.sort(key=lambda t: _latency_seconds(t) or 0, reverse=True)
    return [
        {
            "id": t.run_id,
            "name": t.name,
            "start_time": t.start_time.isoformat() + "Z" if t.start_time else None,
            "latency": _latency_seconds(t),
            "error": t.error,
            **_trace_meta(t),
        }
        for t in traces[:limit]
    ]


# ── 靜態路徑端點（必須在 /{run_id} 動態路由之前） ────────────────────────────

@router.get("/api/traces/timeline")
def trace_timeline(
    prompt_name: str | None = None,
    task_type: str | None = None,
    route_intent: str | None = None,
    days: int = 14,
    db: Session = Depends(get_db),
):
    """每日 avg_quality / avg_latency 走勢，供前端折線圖使用。"""
    from datetime import datetime, timedelta, timezone
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    query = db.query(Trace).filter(
        _agent_execution_root(),
        Trace.start_time >= cutoff,
    )
    if _clean_filter(prompt_name):
        query = query.filter(Trace.prompt_name == prompt_name)
    if _clean_filter(task_type):
        query = query.filter(Trace.task_type == task_type)
    if _clean_filter(route_intent):
        query = query.filter(Trace.route_intent == route_intent)
    traces = query.all()

    buckets: dict[str, dict] = {}
    for t in traces:
        if not t.start_time:
            continue
        day = t.start_time.strftime("%Y-%m-%d")
        b = buckets.setdefault(day, {
            "date": day, "runs": 0, "errors": 0,
            "latencies": [], "quality_scores": [],
        })
        b["runs"] += 1
        if t.error:
            b["errors"] += 1
        lat = _latency_seconds(t)
        if lat is not None:
            b["latencies"].append(lat)
        if t.quality_score is not None:
            b["quality_scores"].append(t.quality_score)

    result = []
    for day in sorted(buckets):
        b = buckets[day]
        lats = b.pop("latencies")
        qs = b.pop("quality_scores")
        b["avg_latency"] = round(sum(lats) / len(lats), 2) if lats else None
        b["avg_quality"] = round(sum(qs) / len(qs), 2) if qs else None
        result.append(b)
    return result


@router.get("/api/traces/compare")
def compare_prompt_versions(
    v1: str,
    v2: str,
    limit: int = 200,
    db: Session = Depends(get_db),
):
    """比較兩個 prompt_version 的品質、延遲、錯誤率。"""
    def _stats(version: str) -> dict:
        traces = (
            db.query(Trace)
            .filter(_agent_execution_root(), Trace.prompt_version == version)
            .order_by(Trace.start_time.desc())
            .limit(limit)
            .all()
        )
        if not traces:
            return {"version": version, "runs": 0, "errors": 0,
                    "avg_latency": None, "avg_quality": None, "error_rate": None}
        latencies = [v for v in (_latency_seconds(t) for t in traces) if v is not None]
        quality = [t.quality_score for t in traces if t.quality_score is not None]
        errors = sum(1 for t in traces if t.error)
        return {
            "version": version,
            "runs": len(traces),
            "errors": errors,
            "error_rate": round(errors / len(traces), 3),
            "avg_latency": round(sum(latencies) / len(latencies), 2) if latencies else None,
            "avg_quality": round(sum(quality) / len(quality), 2) if quality else None,
        }

    return {"v1": _stats(v1), "v2": _stats(v2)}


class BatchScoreResponse(BaseModel):
    queued: int
    message: str


@router.post("/api/traces/batch-score")
async def batch_score_traces(
    limit: int = 50,
    db: Session = Depends(get_db),
):
    """對最近 limit 條 quality_score=null 的 root trace 執行 evaluation_agent 評分。

    細項寫入 quality_detail；overall 寫入 quality_score。
    """
    from agents.evaluation_agent import score_trace

    traces = (
        db.query(Trace)
        .filter(
            _agent_execution_root(),
            Trace.quality_score.is_(None),
            Trace.error.is_(None),
        )
        .order_by(Trace.start_time.desc())
        .limit(limit)
        .all()
    )
    run_ids_context = [(t.run_id, t.task_type or "", t.route_intent or "") for t in traces]

    async def _score_all(items: list[tuple[str, str, str]]) -> None:
        from db import db_session
        for run_id, task_type, route_intent in items:
            try:
                def _read(_run_id=run_id):
                    with db_session() as session:
                        t = session.query(Trace).filter(Trace.run_id == _run_id).first()
                        if not t:
                            return None
                        display_data: dict = {}
                        if t.display:
                            try:
                                display_data = json.loads(t.display)
                            except Exception:
                                pass
                        return display_data

                display_data = await asyncio.to_thread(_read)
                if display_data is None:
                    continue

                score, explanation, detail = await score_trace(
                    display_data,
                    task_type=task_type or "unknown",
                    route_intent=route_intent or None,
                )

                def _write(_run_id=run_id, _score=score, _explanation=explanation, _detail=detail):
                    with db_session() as session:
                        t = session.query(Trace).filter(Trace.run_id == _run_id).first()
                        if t:
                            t.quality_detail = json.dumps(_detail, ensure_ascii=False) if _detail else None
                            t.quality_score = _score
                            if not t.user_feedback:
                                t.user_feedback = _explanation[:500]
                            session.commit()

                await asyncio.to_thread(_write)
            except Exception as exc:
                logger.warning("batch_score failed for %s: %s", run_id, exc)

    asyncio.create_task(_score_all(run_ids_context))
    return {"queued": len(run_ids_context), "message": f"已排入 {len(run_ids_context)} 條追蹤記錄的自動評分。"}


@router.get("/api/traces/score-stats")
def score_stats(db: Session = Depends(get_db)):
    """Score distribution, dimension averages, and queue counts for the Scores page."""
    traces = (
        db.query(Trace)
        .filter(_agent_execution_root())
        .all()
    )

    scored = [t for t in traces if t.quality_score is not None]
    unscored_count = len(traces) - len(scored)

    # Distribution buckets
    buckets = {"0-1": 0, "1-2": 0, "2-3": 0, "3-4": 0, "4-5": 0}
    for t in scored:
        s = t.quality_score
        if s < 1:   buckets["0-1"] += 1
        elif s < 2: buckets["1-2"] += 1
        elif s < 3: buckets["2-3"] += 1
        elif s < 4: buckets["3-4"] += 1
        else:        buckets["4-5"] += 1

    avg_score = round(sum(t.quality_score for t in scored) / len(scored), 2) if scored else None
    low_quality_count = sum(1 for t in scored if t.quality_score < 3)

    # Per-dimension averages from quality_detail JSON
    dim_sums: dict[str, list[float]] = {
        k: [] for k in ["grounding", "task_fit", "completeness", "specificity",
                         "source_quality", "uncertainty_honesty", "format_fit"]
    }
    for t in scored:
        detail = _parse_json(t.quality_detail) if t.quality_detail else None
        if not isinstance(detail, dict):
            continue
        for k in dim_sums:
            v = detail.get(k)
            if isinstance(v, (int, float)):
                dim_sums[k].append(float(v))

    dimension_avgs = {
        k: round(sum(v) / len(v), 2) if v else None
        for k, v in dim_sums.items()
    }

    return {
        "total": len(traces),
        "scored": len(scored),
        "unscored": unscored_count,
        "avg_score": avg_score,
        "low_quality_count": low_quality_count,
        "low_quality_pct": round(low_quality_count / len(scored) * 100, 1) if scored else None,
        "distribution": [{"bucket": k, "count": v} for k, v in buckets.items()],
        "dimension_avgs": dimension_avgs,
    }


class TestRouteRequest(BaseModel):
    message: str
    document_ids: list[int] | None = None


@router.post("/api/traces/test-route")
async def test_route(body: TestRouteRequest):
    """測試路由決策，不執行實際 agent。毫秒內回應。"""
    from agents.router_agent import _keyword_classify, _llm_classify_intent, _route_for_intent

    has_docs = bool(body.document_ids)
    keyword_result = _keyword_classify(body.message, has_docs)

    if keyword_result != "uncertain":
        route = _route_for_intent(keyword_result, body.document_ids)
        path = "keyword"
    else:
        llm_intent = await _llm_classify_intent(body.message, has_docs)
        route = _route_for_intent(llm_intent, body.document_ids)
        path = "llm"

    return {
        "intent": route.intent,
        "agent_name": route.agent_name,
        "prompt_name": route.prompt_name,
        "prompt_version": route.prompt_version,
        "original_intent": route.original_intent,
        "resolved_intent": route.resolved_intent,
        "path": path,
    }


# ── Sessions ──────────────────────────────────────────────────────────────────

@router.get("/api/traces/sessions")
def list_sessions(
    limit: int = 50,
    offset: int = 0,
    route_intent: str | None = None,
    user_id: str | None = None,
    environment: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    order_by: str = "created_at",
    order_dir: str = "desc",
    db: Session = Depends(get_db),
):
    q = db.query(Trace).filter(_agent_execution_root(), Trace.thread_id.isnot(None))

    if environment:
        q = q.filter(Trace.environment == environment)
    if route_intent:
        q = q.filter(Trace.route_intent == route_intent)
    if user_id:
        q = q.filter(Trace.user_id == user_id)
    if date_from:
        try:
            q = q.filter(Trace.start_time >= datetime.fromisoformat(date_from))
        except ValueError:
            pass
    if date_to:
        try:
            q = q.filter(Trace.start_time <= datetime.fromisoformat(date_to))
        except ValueError:
            pass

    traces = q.order_by(Trace.start_time.desc()).limit(2000).all()

    sessions: dict[str, dict] = {}
    for t in traces:
        tid = t.thread_id
        if tid not in sessions:
            sessions[tid] = {
                "thread_id": tid,
                "_route_intents": [],
                "created_at": t.start_time,
                "ended_at": t.end_time,
                "trace_count": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "_quality_scores": [],
                "_user_ids": set(),
            }
        s = sessions[tid]
        s["trace_count"] += 1
        s["input_tokens"] += t.prompt_tokens or 0
        s["output_tokens"] += t.completion_tokens or 0
        if t.route_intent:
            s["_route_intents"].append(t.route_intent)
        if t.quality_score is not None:
            s["_quality_scores"].append(t.quality_score)
        if t.user_id:
            s["_user_ids"].add(t.user_id)
        if t.start_time and s["created_at"] and t.start_time < s["created_at"]:
            s["created_at"] = t.start_time
        if t.end_time and (s["ended_at"] is None or t.end_time > s["ended_at"]):
            s["ended_at"] = t.end_time

    result = []
    for s in sessions.values():
        intents = s.pop("_route_intents")
        qs = s.pop("_quality_scores")
        uid = s.pop("_user_ids")

        task_type = Counter(intents).most_common(1)[0][0] if intents else None
        total_tokens = s["input_tokens"] + s["output_tokens"]
        avg_quality = round(sum(qs) / len(qs), 2) if qs else None
        created_at = s["created_at"]
        ended_at = s["ended_at"]
        duration = (
            round((ended_at - created_at).total_seconds(), 1)
            if created_at and ended_at
            else None
        )
        result.append({
            "thread_id": s["thread_id"],
            "task_type": task_type,
            "created_at": created_at.isoformat() if created_at else None,
            "ended_at": ended_at.isoformat() if ended_at else None,
            "duration_seconds": duration,
            "trace_count": s["trace_count"],
            "input_tokens": s["input_tokens"],
            "output_tokens": s["output_tokens"],
            "total_tokens": total_tokens,
            "avg_quality_score": avg_quality,
            "user_ids": sorted(uid),
        })

    reverse = order_dir.lower() != "asc"
    sort_key_fn = {
        "created_at": lambda x: x["created_at"] or "",
        "duration": lambda x: x["duration_seconds"] or 0,
        "trace_count": lambda x: x["trace_count"],
        "total_tokens": lambda x: x["total_tokens"],
        "avg_quality": lambda x: x["avg_quality_score"] or 0,
    }.get(order_by, lambda x: x["created_at"] or "")
    result.sort(key=sort_key_fn, reverse=reverse)

    total = len(result)
    return {"sessions": result[offset: offset + limit], "total": total}


@router.get("/api/traces/sessions/{thread_id}")
def get_session_detail(thread_id: str, db: Session = Depends(get_db)):
    traces = (
        db.query(Trace)
        .filter(_agent_execution_root(), Trace.thread_id == thread_id)
        .order_by(Trace.start_time.asc())
        .all()
    )
    if not traces:
        raise HTTPException(status_code=404, detail="Session not found")

    input_tokens = sum(t.prompt_tokens or 0 for t in traces)
    output_tokens = sum(t.completion_tokens or 0 for t in traces)
    qs = [t.quality_score for t in traces if t.quality_score is not None]
    user_ids = sorted({t.user_id for t in traces if t.user_id})
    created_at = min((t.start_time for t in traces if t.start_time), default=None)
    ended_at = max((t.end_time for t in traces if t.end_time), default=None)
    intents = [t.route_intent for t in traces if t.route_intent]
    task_type = Counter(intents).most_common(1)[0][0] if intents else None

    return {
        "thread_id": thread_id,
        "task_type": task_type,
        "created_at": created_at.isoformat() if created_at else None,
        "ended_at": ended_at.isoformat() if ended_at else None,
        "duration_seconds": (
            round((ended_at - created_at).total_seconds(), 1)
            if created_at and ended_at
            else None
        ),
        "trace_count": len(traces),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
        "avg_quality_score": round(sum(qs) / len(qs), 2) if qs else None,
        "user_ids": user_ids,
        "traces": [_trace_payload(t) for t in traces],
    }


# ── Users ─────────────────────────────────────────────────────────────────────

@router.get("/api/traces/users")
def list_users(
    limit: int = 50,
    offset: int = 0,
    environment: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    search: str | None = None,
    db: Session = Depends(get_db),
):
    q = db.query(Trace).filter(
        _agent_execution_root(),
        Trace.user_id.isnot(None),
    )
    if environment:
        q = q.filter(Trace.environment == environment)
    if date_from:
        try:
            q = q.filter(Trace.start_time >= datetime.fromisoformat(date_from))
        except ValueError:
            pass
    if date_to:
        try:
            q = q.filter(Trace.start_time <= datetime.fromisoformat(date_to))
        except ValueError:
            pass
    if search:
        q = q.filter(Trace.user_id.ilike(f"%{search}%"))

    traces = q.order_by(Trace.start_time.desc()).limit(5000).all()

    users: dict[str, dict] = {}
    for t in traces:
        uid = t.user_id
        if uid not in users:
            users[uid] = {
                "user_id": uid,
                "first_event": t.start_time,
                "last_event": t.start_time,
                "session_ids": set(),
                "trace_count": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "_quality_scores": [],
            }
        u = users[uid]
        u["trace_count"] += 1
        u["input_tokens"] += t.prompt_tokens or 0
        u["output_tokens"] += t.completion_tokens or 0
        if t.quality_score is not None:
            u["_quality_scores"].append(t.quality_score)
        if t.thread_id:
            u["session_ids"].add(t.thread_id)
        if t.start_time:
            if u["first_event"] is None or t.start_time < u["first_event"]:
                u["first_event"] = t.start_time
            if u["last_event"] is None or t.start_time > u["last_event"]:
                u["last_event"] = t.start_time

    result = []
    for u in users.values():
        qs = u.pop("_quality_scores")
        sids = u.pop("session_ids")
        result.append({
            "user_id": u["user_id"],
            "first_event": u["first_event"].isoformat() if u["first_event"] else None,
            "last_event": u["last_event"].isoformat() if u["last_event"] else None,
            "session_count": len(sids),
            "trace_count": u["trace_count"],
            "input_tokens": u["input_tokens"],
            "output_tokens": u["output_tokens"],
            "total_tokens": u["input_tokens"] + u["output_tokens"],
            "avg_quality_score": round(sum(qs) / len(qs), 2) if qs else None,
        })

    result.sort(key=lambda x: x["last_event"] or "", reverse=True)
    total = len(result)
    return {"users": result[offset: offset + limit], "total": total}


@router.get("/api/traces/users/{user_id}")
def get_user_detail(user_id: str, db: Session = Depends(get_db)):
    # Sessions for this user
    traces = (
        db.query(Trace)
        .filter(_agent_execution_root(), Trace.user_id == user_id)
        .order_by(Trace.start_time.desc())
        .limit(500)
        .all()
    )
    if not traces:
        raise HTTPException(status_code=404, detail="User not found")

    # Aggregate per session (thread_id)
    sessions: dict[str, dict] = {}
    for t in traces:
        tid = t.thread_id or t.run_id
        if tid not in sessions:
            sessions[tid] = {
                "thread_id": tid,
                "task_type": t.route_intent,
                "created_at": t.start_time,
                "trace_count": 0,
                "total_tokens": 0,
                "_quality_scores": [],
            }
        s = sessions[tid]
        s["trace_count"] += 1
        s["total_tokens"] += (t.prompt_tokens or 0) + (t.completion_tokens or 0)
        if t.quality_score is not None:
            s["_quality_scores"].append(t.quality_score)
        if t.start_time and s["created_at"] and t.start_time < s["created_at"]:
            s["created_at"] = t.start_time

    session_list = []
    for s in sessions.values():
        qs = s.pop("_quality_scores")
        s["avg_quality_score"] = round(sum(qs) / len(qs), 2) if qs else None
        s["created_at"] = s["created_at"].isoformat() if s["created_at"] else None
        session_list.append(s)

    session_list.sort(key=lambda x: x["created_at"] or "", reverse=True)

    input_tokens = sum(t.prompt_tokens or 0 for t in traces)
    output_tokens = sum(t.completion_tokens or 0 for t in traces)
    qs_all = [t.quality_score for t in traces if t.quality_score is not None]

    return {
        "user_id": user_id,
        "first_event": min((t.start_time for t in traces if t.start_time), default=None),
        "last_event": max((t.start_time for t in traces if t.start_time), default=None),
        "session_count": len(sessions),
        "trace_count": len(traces),
        "total_tokens": input_tokens + output_tokens,
        "avg_quality_score": round(sum(qs_all) / len(qs_all), 2) if qs_all else None,
        "sessions": session_list,
    }


# ── 動態路由（必須在所有靜態路徑之後） ────────────────────────────────────────

@router.get("/api/traces/{run_id}")
def get_trace_detail(run_id: str, db: Session = Depends(get_db)):
    trace = db.query(Trace).filter(Trace.run_id == run_id).first()
    if not trace:
        raise HTTPException(status_code=404, detail="Trace not found")

    children = (
        db.query(Trace)
        .filter(Trace.parent_run_id == run_id)
        .order_by(Trace.start_time.asc())
        .limit(200)
        .all()
    )
    payload = _trace_payload(trace, include_raw=True)
    payload["children"] = [_trace_payload(child, include_raw=True) for child in children]
    return payload


@router.patch("/api/traces/{run_id}/feedback")
def update_trace_feedback(run_id: str, body: TraceFeedbackRequest, db: Session = Depends(get_db)):
    trace = db.query(Trace).filter(Trace.run_id == run_id).first()
    if not trace:
        raise HTTPException(status_code=404, detail="Trace not found")
    if body.quality_score is not None and not 0 <= body.quality_score <= 5:
        raise HTTPException(status_code=400, detail="quality_score must be between 0 and 5")

    trace.quality_score = body.quality_score
    trace.user_feedback = body.user_feedback.strip() if body.user_feedback else None
    db.commit()
    db.refresh(trace)
    return _trace_payload(trace, include_raw=True)


@router.post("/api/eval/run")
async def run_eval():
    """執行路由準確性評估，返回 accuracy + 失敗案例。"""
    try:
        from eval.runner import run_eval_suite
        report = await run_eval_suite()
        return report
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
