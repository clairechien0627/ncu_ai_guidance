"""Evaluation-related routes mounted on the shared trace admin router."""

from fastapi import Depends
from sqlalchemy.orm import Session

from api.trace_router import router
from db import get_db
from services.evaluation.analytics import EvaluationAnalyticsService
from services.evaluation.runs import EvaluationRunService


def _evaluation_worker():
    from api import traces as traces_api
    return traces_api.EvaluationWorker


@router.post("/api/traces/batch-score")
async def batch_score_traces(
    limit: int = 50,
    db: Session = Depends(get_db),
):
    """對最近 limit 條未評分 root trace 建立 evaluation run 並背景執行。"""
    run = EvaluationRunService.create_trace_batch(db, limit=limit)
    if run is None:
        return {"queued": 0, "message": "沒有需要自動評分的追蹤記錄。"}
    await _evaluation_worker().wakeup(run.total_count)
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
    await _evaluation_worker().wakeup(retried)
    return {"retried": retried, "message": f"已重排 {retried} 個失敗項目。"}
