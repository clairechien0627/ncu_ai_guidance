import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import or_, and_, select
from sqlalchemy.orm import Session

from db import get_db, Trace, Observation, Score, TraceV2
from services.datasets import DatasetService
from services.evaluation.analytics import EvaluationAnalyticsService
from services.evaluation.reports import EvaluationReportService
from services.evaluation.runs import EvaluationRunService
from services.evaluation.worker import EvaluationWorker
from services.evaluation.experiments import ExperimentRunService
from services.trace_repositories import ScoreRepository
from services.trace_read.service import TraceReadService

logger = logging.getLogger(__name__)

from api.dependencies import require_admin
router = APIRouter(dependencies=[Depends(require_admin)])


def _agent_execution_root():
    """
    SQLAlchemy filter condition that matches each routed agent execution root.

    Supports both old data where the task agent was the DB root
    (trace_id=None) and new data where the task agent is a child of
    router_agent.
    """
    router_observation_ids = (
        select(Trace.observation_id).where(Trace.agent_name == "router_agent").scalar_subquery()
    )
    return or_(
        Trace.trace_id.in_(router_observation_ids),
        and_(Trace.trace_id.is_(None), Trace.agent_name != "router_agent"),
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
        "id": t.observation_id,
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
        "prompt_tokens": t.prompt_tokens,
        "completion_tokens": t.completion_tokens,
        "input_cost": t.input_cost,
        "output_cost": t.output_cost,
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
            "trace_id": t.trace_id,
            "thread_id": t.thread_id,
            "document_ids": _parse_json(t.document_ids),
            "inputs_raw": _parse_json(t.inputs),
            "outputs_raw": _parse_json(t.outputs),
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
    return TraceReadService(db).document_traces(doc_id)


@router.get("/api/traces/environments")
def list_environments(db: Session = Depends(get_db)):
    """Return distinct environment values present in root traces."""
    return TraceReadService(db).environments()


class BatchDeleteTracesRequest(BaseModel):
    ids: list[str]  # trace_ids to delete


@router.delete("/api/traces/batch")
def batch_delete_traces(
    payload: BatchDeleteTracesRequest,
    db: Session = Depends(get_db),
):
    trace_ids = payload.ids
    if not trace_ids:
        return {"deleted": 0}
    db.query(Score).filter(Score.trace_id.in_(trace_ids)).delete(synchronize_session=False)
    db.query(Observation).filter(Observation.trace_id.in_(trace_ids)).delete(synchronize_session=False)
    db.query(TraceV2).filter(TraceV2.trace_id.in_(trace_ids)).delete(synchronize_session=False)
    deleted = db.query(Trace).filter(Trace.observation_id.in_(trace_ids)).delete(synchronize_session=False)
    db.commit()
    return {"deleted": deleted}


@router.get("/api/traces/names")
def list_trace_names(db: Session = Depends(get_db)):
    """Return unique trace names with counts, sorted by count desc."""
    from sqlalchemy import func as _func
    rows = (
        db.query(TraceV2.name, _func.count(TraceV2.id).label("cnt"))
        .group_by(TraceV2.name)
        .order_by(_func.count(TraceV2.id).desc())
        .all()
    )
    return [{"name": name, "count": cnt} for name, cnt in rows if name]


@router.get("/api/traces/user-ids")
def list_trace_user_ids(db: Session = Depends(get_db)):
    """Return unique user_ids with counts."""
    from sqlalchemy import func as _func
    rows = (
        db.query(TraceV2.user_id, _func.count(TraceV2.id).label("cnt"))
        .filter(TraceV2.user_id.isnot(None))
        .group_by(TraceV2.user_id)
        .order_by(_func.count(TraceV2.id).desc())
        .all()
    )
    return [{"user_id": uid, "count": cnt} for uid, cnt in rows]


@router.get("/api/traces/tags")
def list_trace_tags(db: Session = Depends(get_db)):
    """Return sorted unique tag values across all traces_v2."""
    import json as _json
    rows = db.query(TraceV2.tags).filter(TraceV2.tags.isnot(None)).all()
    tags: set[str] = set()
    for (tag_json,) in rows:
        if not tag_json:
            continue
        try:
            parsed = _json.loads(tag_json) if isinstance(tag_json, str) else tag_json
        except Exception:
            continue
        if isinstance(parsed, list):
            for t in parsed:
                if t and isinstance(t, str):
                    tags.add(t)
    return sorted(tags)


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
    agent_name: str | None = None,
    environment: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    tag: str | None = None,
    tags: str | None = None,
    name: str | None = None,
    names: str | None = None,
    user_id: str | None = None,
    user_ids: str | None = None,
    level: str | None = None,
    min_quality: float | None = None,
    min_tokens: int | None = None,
    max_tokens: int | None = None,
    min_input_tokens: int | None = None,
    min_output_tokens: int | None = None,
    db: Session = Depends(get_db),
):
    return TraceReadService(db).list_traces(
        limit=limit,
        offset=offset,
        task_type=task_type,
        route_intent=route_intent,
        prompt_name=prompt_name,
        prompt_version=prompt_version,
        status=status,
        min_latency=min_latency,
        max_quality=max_quality,
        min_quality=min_quality,
        has_score=has_score,
        original_intent=original_intent,
        resolved_intent=resolved_intent,
        agent_name=agent_name,
        environment=environment,
        date_from=date_from,
        date_to=date_to,
        tag=tag,
        tags=tags,
        name=name,
        names=names,
        user_id=user_id,
        user_ids=user_ids,
        level=level,
        min_tokens=min_tokens,
        max_tokens=max_tokens,
        min_input_tokens=min_input_tokens,
        min_output_tokens=min_output_tokens,
    )


@router.get("/api/traces/stats")
def trace_stats(days: int = 7, db: Session = Depends(get_db)):
    return TraceReadService(db).stats(days=days)


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
    return TraceReadService(db).grouped("task_type")


@router.get("/api/traces/by-route-intent")
def traces_by_route_intent(db: Session = Depends(get_db)):
    return TraceReadService(db).grouped("route_intent")


@router.get("/api/traces/by-prompt")
def traces_by_prompt(db: Session = Depends(get_db)):
    return TraceReadService(db).grouped("prompt_name")


@router.get("/api/traces/by-prompt-version")
def traces_by_prompt_version(db: Session = Depends(get_db)):
    return TraceReadService(db).grouped("prompt_version")


@router.get("/api/traces/errors")
def trace_errors(limit: int = 40, db: Session = Depends(get_db)):
    return TraceReadService(db).errors(limit=limit)


@router.get("/api/traces/observations")
def list_observations(
    limit: int = 100,
    offset: int = 0,
    run_type: str | None = None,
    db: Session = Depends(get_db),
):
    """Return child spans (LLM calls, tool calls, chains) — i.e. non-root traces."""
    return TraceReadService(db).observations(limit=limit, offset=offset, run_type=run_type)


@router.get("/api/traces/observations/stats")
def observations_stats(db: Session = Depends(get_db)):
    """Aggregate counts and token totals for the Observations page KPI row."""
    return TraceReadService(db).observation_stats()


@router.get("/api/traces/slow-runs")
def slow_runs(limit: int = 40, min_latency: float = 10, db: Session = Depends(get_db)):
    return TraceReadService(db).slow_runs(limit=limit, min_latency=min_latency)


# ── 靜態路徑端點（必須在 /{observation_id} 動態路由之前） ────────────────────────────

@router.get("/api/traces/timeline")
def trace_timeline(
    prompt_name: str | None = None,
    task_type: str | None = None,
    route_intent: str | None = None,
    days: int = 14,
    db: Session = Depends(get_db),
):
    """每日 avg_quality / avg_latency 走勢，供前端折線圖使用。"""
    return TraceReadService(db).timeline(
        prompt_name=prompt_name,
        task_type=task_type,
        route_intent=route_intent,
        days=days,
    )


@router.get("/api/traces/compare")
def compare_prompt_versions(
    v1: str,
    v2: str,
    limit: int = 200,
    db: Session = Depends(get_db),
):
    """比較兩個 prompt_version 的品質、延遲、錯誤率。"""
    return TraceReadService(db).compare_prompt_versions(v1, v2, limit=limit)


class BatchScoreResponse(BaseModel):
    queued: int
    message: str


class DatasetCreateRequest(BaseModel):
    name: str
    description: str | None = None
    source: str | None = None
    metadata: dict | None = None


class DatasetManualItemRequest(BaseModel):
    input: dict | None = None
    output: dict | str | None = None
    expected_output: dict | str | None = None
    context: dict | None = None
    tags: list[str] | None = None
    metadata: dict | None = None


class DatasetTraceItemRequest(BaseModel):
    trace_id: str
    tags: list[str] | None = None


class DatasetLowQualityRequest(BaseModel):
    max_quality: float = 3.0
    limit: int = 50


class DatasetEvalRunRequest(BaseModel):
    name: str | None = None
    metadata: dict | None = None


class ExperimentReplayRequest(BaseModel):
    dataset_id: str
    name: str | None = None
    target_agent: str | None = None
    model: str | None = None
    prompt_name: str | None = None
    prompt_version: str | None = None
    runtime_config: dict | None = None
    metadata: dict | None = None


class ExperimentEvalRequest(BaseModel):
    name: str | None = None
    metadata: dict | None = None


@router.post("/api/traces/batch-score")
async def batch_score_traces(
    limit: int = 50,
    db: Session = Depends(get_db),
):
    """對最近 limit 條未評分 root trace 建立 evaluation run 並背景執行。"""
    run = EvaluationRunService.create_trace_batch(db, limit=limit)
    if run is None:
        return {"queued": 0, "message": "沒有需要自動評分的追蹤記錄。"}
    await EvaluationWorker.wakeup(run.total_count)
    return {"queued": run.total_count, "message": f"已排入 {run.total_count} 條追蹤記錄的自動評分。"}


@router.get("/api/evaluations/runs")
def list_evaluation_runs(
    limit: int = 50,
    offset: int = 0,
    status: str | None = None,
    name: str | None = None,
    scope: str | None = None,
    db: Session = Depends(get_db),
):
    return EvaluationRunService.list_runs(
        db,
        limit=limit,
        offset=offset,
        status=status,
        name=name,
        scope=scope,
    )


@router.get("/api/evaluations/compare")
def compare_evaluation_runs(left: str, right: str, db: Session = Depends(get_db)):
    return EvaluationAnalyticsService.compare_eval_runs(db, left, right)


@router.get("/api/evaluations/runs/{eval_run_id}/score-stats")
def get_evaluation_run_score_stats(eval_run_id: str, db: Session = Depends(get_db)):
    return EvaluationAnalyticsService.eval_run_score_stats(db, eval_run_id)


@router.get("/api/evaluations/runs/{eval_run_id}")
def get_evaluation_run(eval_run_id: str, db: Session = Depends(get_db)):
    return EvaluationRunService.get_run_detail(db, eval_run_id)


@router.post("/api/evaluations/runs/{eval_run_id}/retry")
async def retry_evaluation_run(eval_run_id: str, db: Session = Depends(get_db)):
    """把指定 eval run 裡的 failed items 重設為 pending 並觸發 worker。"""
    retried = EvaluationRunService.retry_failed_items(db, eval_run_id)
    if retried == 0:
        return {"retried": 0, "message": "沒有可重試的失敗項目。"}
    await EvaluationWorker.wakeup(retried)
    return {"retried": retried, "message": f"已重排 {retried} 個失敗項目。"}


@router.post("/api/experiments/dataset-replays")
async def create_dataset_replay(body: ExperimentReplayRequest, db: Session = Depends(get_db)):
    run = ExperimentRunService.create_dataset_replay(
        db,
        dataset_id=body.dataset_id,
        name=body.name or "dataset-replay",
        target_agent=body.target_agent,
        model=body.model,
        prompt_name=body.prompt_name,
        prompt_version=body.prompt_version,
        runtime_config=body.runtime_config,
        metadata=body.metadata,
    )
    if run is None:
        return {"queued": 0, "experiment_run_id": None, "message": "Dataset 沒有可 replay 的樣本。"}
    await EvaluationWorker.wakeup(run.total_count)
    return {
        "queued": run.total_count,
        "experiment_run_id": run.experiment_run_id,
        "message": f"已排入 {run.total_count} 筆 dataset items 的 replay。",
    }


@router.get("/api/experiments/runs")
def list_experiment_runs(
    limit: int = 50,
    offset: int = 0,
    status: str | None = None,
    dataset_id: str | None = None,
    name: str | None = None,
    db: Session = Depends(get_db),
):
    return ExperimentRunService.list_runs(
        db,
        limit=limit,
        offset=offset,
        status=status,
        dataset_id=dataset_id,
        name=name,
    )


@router.get("/api/experiments/runs/{experiment_run_id}/score-stats")
def get_experiment_score_stats(experiment_run_id: str, db: Session = Depends(get_db)):
    return EvaluationAnalyticsService.experiment_score_stats(db, experiment_run_id)


@router.get("/api/experiments/runs/{experiment_run_id}/report")
def get_experiment_report(experiment_run_id: str, limit: int = 50, db: Session = Depends(get_db)):
    return EvaluationReportService.experiment_report(db, experiment_run_id, limit=limit)


@router.post("/api/experiments/runs/{experiment_run_id}/eval")
async def create_experiment_eval_run(
    experiment_run_id: str,
    body: ExperimentEvalRequest | None = None,
    db: Session = Depends(get_db),
):
    body = body or ExperimentEvalRequest()
    run = ExperimentRunService.create_eval_run_for_experiment(
        db,
        experiment_run_id,
        name=body.name or "experiment-evaluation",
        metadata=body.metadata,
    )
    if run is None:
        return {"queued": 0, "eval_run_id": None, "message": "Experiment 沒有已完成的 replay item 可評估。"}
    await EvaluationWorker.wakeup(run.total_count)
    return {
        "queued": run.total_count,
        "eval_run_id": run.eval_run_id,
        "message": f"已排入 {run.total_count} 筆 experiment outputs 的評估。",
    }


@router.get("/api/experiments/compare")
def compare_experiment_runs(a: str, b: str, db: Session = Depends(get_db)):
    """Compare two experiment runs item-by-item. ?a=exp-xxx&b=exp-yyy"""
    return ExperimentRunService.compare_runs(db, a, b)


@router.get("/api/experiments/compare/report")
def compare_experiment_runs_report(left: str, right: str, limit: int = 50, db: Session = Depends(get_db)):
    return EvaluationReportService.experiment_comparison_report(db, left, right, limit=limit)


@router.get("/api/experiments/runs/{experiment_run_id}")
def get_experiment_run(experiment_run_id: str, db: Session = Depends(get_db)):
    return ExperimentRunService.get_run_detail(db, experiment_run_id)


@router.get("/api/datasets")
def list_datasets(
    limit: int = 50,
    offset: int = 0,
    include_archived: bool = False,
    db: Session = Depends(get_db),
):
    return DatasetService.list_datasets(db, limit=limit, offset=offset, include_archived=include_archived)


@router.post("/api/datasets")
def create_dataset(body: DatasetCreateRequest, db: Session = Depends(get_db)):
    row = DatasetService.create_dataset(
        db,
        name=body.name,
        description=body.description,
        source=body.source,
        metadata=body.metadata,
    )
    return DatasetService.get_dataset_detail(db, row.dataset_id)


@router.get("/api/datasets/{dataset_id}/score-stats")
def get_dataset_score_stats(dataset_id: str, db: Session = Depends(get_db)):
    return EvaluationAnalyticsService.dataset_score_stats(db, dataset_id)


@router.get("/api/datasets/{dataset_id}/regression-cases")
def get_dataset_regression_cases(dataset_id: str, limit: int = 50, threshold: float = 3.0, db: Session = Depends(get_db)):
    return EvaluationReportService.dataset_regression_cases(db, dataset_id, limit=limit, threshold=threshold)


@router.get("/api/datasets/{dataset_id}/eval-runs")
def get_dataset_eval_runs(
    dataset_id: str,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    return EvaluationAnalyticsService.dataset_eval_runs(db, dataset_id, limit=limit, offset=offset)


@router.get("/api/datasets/{dataset_id}/items/{dataset_item_id}/scores")
def get_dataset_item_scores(dataset_id: str, dataset_item_id: str, db: Session = Depends(get_db)):
    return EvaluationAnalyticsService.dataset_item_score_history(db, dataset_id, dataset_item_id)


@router.get("/api/datasets/{dataset_id}")
def get_dataset(dataset_id: str, db: Session = Depends(get_db)):
    return DatasetService.get_dataset_detail(db, dataset_id)


@router.post("/api/datasets/{dataset_id}/items/from-trace")
def add_dataset_item_from_trace(dataset_id: str, body: DatasetTraceItemRequest, db: Session = Depends(get_db)):
    item = DatasetService.add_trace_item(db, dataset_id, body.trace_id, tags=body.tags)
    return DatasetService.get_dataset_detail(db, item.dataset_id)


@router.post("/api/datasets/{dataset_id}/items/from-low-quality")
def add_dataset_items_from_low_quality(dataset_id: str, body: DatasetLowQualityRequest, db: Session = Depends(get_db)):
    return DatasetService.add_low_quality_traces(
        db,
        dataset_id,
        max_quality=body.max_quality,
        limit=body.limit,
    )


@router.post("/api/datasets/{dataset_id}/items")
def add_dataset_item_manual(dataset_id: str, body: DatasetManualItemRequest, db: Session = Depends(get_db)):
    """手動新增 dataset item（不需要 source trace）。"""
    DatasetService.add_manual_item(
        db,
        dataset_id,
        input=body.input,
        output=body.output,
        expected_output=body.expected_output,
        context=body.context,
        tags=body.tags,
        metadata=body.metadata,
    )
    return DatasetService.get_dataset_detail(db, dataset_id)


@router.delete("/api/datasets/{dataset_id}/items/{item_id}")
def delete_dataset_item(dataset_id: str, item_id: str, db: Session = Depends(get_db)):
    """Archive a dataset item (soft delete)."""
    DatasetService.archive_item(db, dataset_id, item_id)
    return {"deleted": item_id}


@router.post("/api/datasets/{dataset_id}/eval-runs")
async def create_dataset_eval_run(dataset_id: str, body: DatasetEvalRunRequest | None = None, db: Session = Depends(get_db)):
    body = body or DatasetEvalRunRequest()
    run = EvaluationRunService.create_dataset_run(
        db,
        dataset_id=dataset_id,
        name=body.name or "dataset-evaluation",
        metadata=body.metadata,
    )
    if run is None:
        return {"queued": 0, "eval_run_id": None, "message": "Dataset 沒有可評估的樣本。"}
    await EvaluationWorker.wakeup(run.total_count)
    return {"queued": run.total_count, "eval_run_id": run.eval_run_id, "message": f"已排入 {run.total_count} 筆 dataset items 的評估。"}


@router.get("/api/traces/score-stats")
def score_stats(db: Session = Depends(get_db)):
    """Score distribution, dimension averages, and queue counts for the Scores page."""
    return TraceReadService(db).score_stats()


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


# ── Threads ───────────────────────────────────────────────────────────────────

@router.get("/api/traces/threads")
def list_threads(
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
    return TraceReadService(db).sessions(
        limit=limit,
        offset=offset,
        route_intent=route_intent,
        user_id=user_id,
        environment=environment,
        date_from=date_from,
        date_to=date_to,
        order_by=order_by,
        order_dir=order_dir,
    )


@router.get("/api/traces/threads/{thread_id}")
def get_thread_detail(thread_id: str, db: Session = Depends(get_db)):
    return TraceReadService(db).session_detail(thread_id)


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
    return TraceReadService(db).users(
        limit=limit,
        offset=offset,
        environment=environment,
        date_from=date_from,
        date_to=date_to,
        search=search,
    )


@router.get("/api/traces/users/{user_id}")
def get_user_detail(user_id: str, db: Session = Depends(get_db)):
    return TraceReadService(db).user_detail(user_id)


# ── 動態路由（必須在所有靜態路徑之後） ────────────────────────────────────────

@router.get("/api/traces/{observation_id}")
def get_trace_detail(observation_id: str, db: Session = Depends(get_db)):
    return TraceReadService(db).trace_detail(observation_id)


@router.patch("/api/traces/{observation_id}/feedback")
def update_trace_feedback(observation_id: str, body: TraceFeedbackRequest, db: Session = Depends(get_db)):
    trace = db.query(Trace).filter(Trace.observation_id == observation_id).first()
    if not trace:
        raise HTTPException(status_code=404, detail="Trace not found")
    if body.quality_score is not None and not 0 <= body.quality_score <= 5:
        raise HTTPException(status_code=400, detail="quality_score must be between 0 and 5")

    trace.quality_score = body.quality_score
    trace.user_feedback = body.user_feedback.strip() if body.user_feedback else None
    if body.quality_score is not None:
        ScoreRepository.upsert_score(db, {
            "score_id": f"{observation_id}:overall:annotation",
            "trace_id": observation_id,
            "name": "overall",
            "value": body.quality_score,
            "data_type": "NUMERIC",
            "source": "ANNOTATION",
            "comment": trace.user_feedback,
            "metadata": {"source": "trace_feedback"},
        }, sync_legacy_cache=False)
    if trace.user_feedback:
        ScoreRepository.upsert_score(db, {
            "score_id": f"{observation_id}:feedback:annotation",
            "trace_id": observation_id,
            "name": "feedback",
            "string_value": trace.user_feedback,
            "data_type": "TEXT",
            "source": "ANNOTATION",
            "comment": trace.user_feedback,
            "metadata": {"source": "trace_feedback"},
        }, sync_legacy_cache=False)
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
