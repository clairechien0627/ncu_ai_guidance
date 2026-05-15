import asyncio
import json
import logging

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
    # document_ids is JSONB (migration 001). Use the @> containment operator
    # which is index-supported via idx_traces_document_ids_gin.
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
    original_intent: str | None = None,
    resolved_intent: str | None = None,
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
def trace_stats(db: Session = Depends(get_db)):
    root = _agent_execution_root()
    total = db.query(func.count(Trace.id)).filter(root).scalar() or 0
    errors = db.query(func.count(Trace.id)).filter(root, Trace.error.isnot(None)).scalar() or 0
    traces = db.query(Trace).filter(root).order_by(Trace.start_time.desc()).limit(1000).all()
    latencies = [v for v in (_latency_seconds(t) for t in traces) if v is not None]
    return {
        "total_runs": total,
        "error_runs": errors,
        "success_runs": total - errors,
        "avg_latency": round(sum(latencies) / len(latencies), 2) if latencies else None,
        "avg_tool_count": round(sum(t.tool_count or 0 for t in traces) / len(traces), 2) if traces else 0,
        "avg_llm_call_count": round(sum(t.llm_call_count or 0 for t in traces) / len(traces), 2) if traces else 0,
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
                with db_session() as session:
                    t = session.query(Trace).filter(Trace.run_id == run_id).first()
                    if not t:
                        continue
                    display_data: dict = {}
                    if t.display:
                        try:
                            display_data = json.loads(t.display)
                        except Exception:
                            pass
                    score, explanation, detail = await score_trace(
                        display_data,
                        task_type=task_type or "unknown",
                        route_intent=route_intent or None,
                    )
                    t.quality_detail = json.dumps(detail, ensure_ascii=False) if detail else None
                    t.quality_score = score
                    if not t.user_feedback:
                        t.user_feedback = explanation[:500]
                    session.commit()
            except Exception as exc:
                logger.warning("batch_score failed for %s: %s", run_id, exc)

    asyncio.create_task(_score_all(run_ids_context))
    return {"queued": len(run_ids_context), "message": f"已排入 {len(run_ids_context)} 條追蹤記錄的自動評分。"}


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
