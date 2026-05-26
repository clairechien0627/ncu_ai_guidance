import json
import logging

from fastapi import Depends, HTTPException, Query
from sqlalchemy.orm import Session

from api.trace_router import router
from api.trace_models import (
    BatchDeleteTracesRequest,
    BatchScoreResponse,
    DatasetCreateRequest,
    DatasetEvalRunRequest,
    DatasetLowQualityRequest,
    DatasetManualItemRequest,
    DatasetTraceItemRequest,
    ExperimentEvalRequest,
    ExperimentReplayRequest,
    TraceBookmarkRequest,
    TraceFeedbackRequest,
)
from db import get_db, Observation, Score, TraceV2
from services.evaluation.worker import EvaluationWorker
from services.trace_repositories import ScoreRepository
from services.trace_read.service import TraceReadService
from api.trace_evaluation_routes import (
    batch_score_traces,
    compare_evaluation_runs,
    get_evaluation_run,
    get_evaluation_run_score_stats,
    list_evaluation_runs,
    retry_evaluation_run,
)
from api.trace_experiment_routes import (
    compare_experiment_runs,
    compare_experiment_runs_report,
    create_dataset_replay,
    create_experiment_eval_run,
    get_experiment_report,
    get_experiment_run,
    get_experiment_score_stats,
    list_experiment_runs,
)
from api.trace_dataset_routes import (
    add_dataset_item_from_trace,
    add_dataset_item_manual,
    add_dataset_items_from_low_quality,
    create_dataset,
    create_dataset_eval_run,
    delete_dataset_item,
    get_dataset,
    get_dataset_eval_runs,
    get_dataset_item_scores,
    get_dataset_regression_cases,
    get_dataset_score_stats,
    list_datasets,
)

logger = logging.getLogger(__name__)


@router.get("/api/documents/{doc_id}/traces")
def get_document_traces(doc_id: int, db: Session = Depends(get_db)):
    return TraceReadService(db).document_traces(doc_id)


@router.get("/api/traces/environments")
def list_environments(db: Session = Depends(get_db)):
    """Return distinct environment values present in root traces."""
    return TraceReadService(db).environments()


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
    deleted = db.query(TraceV2).filter(TraceV2.trace_id.in_(trace_ids)).delete(synchronize_session=False)
    db.commit()
    return {"deleted": deleted}


@router.patch("/api/traces/{trace_id}/bookmark")
def update_trace_bookmark(trace_id: str, body: TraceBookmarkRequest, db: Session = Depends(get_db)):
    row = db.query(TraceV2).filter(TraceV2.trace_id == trace_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Trace not found")
    row.bookmarked = bool(body.bookmarked)
    db.commit()
    return TraceReadService(db).trace_detail(trace_id)


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
    prompt_name: str | None = None,
    prompt_version: str | None = None,
    status: str | None = None,
    min_latency: float | None = None,
    max_quality: float | None = None,
    has_score: bool | None = None,
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
    bookmarked: bool | None = None,
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
        prompt_name=prompt_name,
        prompt_version=prompt_version,
        status=status,
        min_latency=min_latency,
        max_quality=max_quality,
        min_quality=min_quality,
        has_score=has_score,
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
        bookmarked=bookmarked,
        min_tokens=min_tokens,
        max_tokens=max_tokens,
        min_input_tokens=min_input_tokens,
        min_output_tokens=min_output_tokens,
    )


@router.get("/api/traces/stats")
def trace_stats(days: int = 7, db: Session = Depends(get_db)):
    return TraceReadService(db).stats(days=days)


@router.get("/api/traces/by-agent")
def traces_by_agent(db: Session = Depends(get_db)):
    return TraceReadService(db).grouped("agent_name")


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
    obs_type: str | None = Query(default=None, alias="type"),
    db: Session = Depends(get_db),
):
    """Return observations (LLM calls, tool calls, spans) for the Observations page."""
    return TraceReadService(db).observations(limit=limit, offset=offset, obs_type=obs_type)


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
    agent_name: str | None = None,
    days: int = 14,
    db: Session = Depends(get_db),
):
    """每日 avg_quality / avg_latency 走勢，供前端折線圖使用。"""
    return TraceReadService(db).timeline(
        prompt_name=prompt_name,
        agent_name=agent_name,
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


@router.get("/api/traces/score-stats")
def score_stats(db: Session = Depends(get_db)):
    """Score distribution, dimension averages, and queue counts for the Scores page."""
    return TraceReadService(db).score_stats()


# ── Threads ───────────────────────────────────────────────────────────────────

@router.get("/api/traces/threads")
def list_threads(
    limit: int = 50,
    offset: int = 0,
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
    trace_v2 = db.query(TraceV2).filter(TraceV2.trace_id == observation_id).first()
    if not trace_v2:
        raise HTTPException(status_code=404, detail="Trace not found")
    if body.quality_score is not None and not 0 <= body.quality_score <= 5:
        raise HTTPException(status_code=400, detail="quality_score must be between 0 and 5")

    feedback_text = body.user_feedback.strip() if body.user_feedback else None
    if body.quality_score is not None:
        ScoreRepository.upsert_score(db, {
            "score_id": f"{observation_id}:overall:annotation",
            "trace_id": observation_id,
            "name": "overall",
            "value": body.quality_score,
            "data_type": "NUMERIC",
            "source": "ANNOTATION",
            "comment": feedback_text,
            "environment": trace_v2.environment,
            "thread_id": trace_v2.thread_id,
            "metadata": {"source": "trace_feedback"},
        })
    if feedback_text:
        ScoreRepository.upsert_score(db, {
            "score_id": f"{observation_id}:feedback:annotation",
            "trace_id": observation_id,
            "name": "feedback",
            "string_value": feedback_text,
            "data_type": "TEXT",
            "source": "ANNOTATION",
            "comment": feedback_text,
            "environment": trace_v2.environment,
            "thread_id": trace_v2.thread_id,
            "metadata": {"source": "trace_feedback"},
        })
    db.commit()
    return TraceReadService(db).trace_detail(observation_id)


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
