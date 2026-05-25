import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from db import get_db, Observation, Score, TraceV2
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


class TraceFeedbackRequest(BaseModel):
    quality_score: float | None = None
    user_feedback: str | None = None


class TraceBookmarkRequest(BaseModel):
    bookmarked: bool


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


class BatchScoreResponse(BaseModel):
    queued: int
    message: str


class DatasetCreateRequest(BaseModel):
    name: str
    description: str | None = None
    source: str | None = None
    metadata: dict | None = None
    input_schema: dict | None = None
    expected_output_schema: dict | None = None


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
        input_schema=body.input_schema,
        expected_output_schema=body.expected_output_schema,
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
