"""Experiment replay and comparison routes mounted on the shared trace admin router."""

from fastapi import Depends
from sqlalchemy.orm import Session

from api.trace_models import ExperimentEvalRequest, ExperimentReplayRequest
from api.trace_router import router
from db import get_db
from services.evaluation.analytics import EvaluationAnalyticsService
from services.evaluation.experiments import ExperimentRunService
from services.evaluation.reports import EvaluationReportService
from services.evaluation.runs import EvaluationRunService


def _evaluation_worker():
    from api import traces as traces_api
    return traces_api.EvaluationWorker


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
    await _evaluation_worker().wakeup(run.total_count)
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
    await _evaluation_worker().wakeup(run.total_count)
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
