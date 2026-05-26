"""Dataset routes mounted on the shared trace admin router."""

from fastapi import Depends
from sqlalchemy.orm import Session

from api.trace_models import (
    DatasetCreateRequest,
    DatasetEvalRunRequest,
    DatasetLowQualityRequest,
    DatasetManualItemRequest,
    DatasetTraceItemRequest,
)
from api.trace_router import router
from db import get_db
from services.datasets import DatasetService
from services.evaluation.analytics import EvaluationAnalyticsService
from services.evaluation.reports import EvaluationReportService
from services.evaluation.runs import EvaluationRunService


def _evaluation_worker():
    from api import traces as traces_api
    return traces_api.EvaluationWorker


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
    await _evaluation_worker().wakeup(run.total_count)
    return {"queued": run.total_count, "eval_run_id": run.eval_run_id, "message": f"已排入 {run.total_count} 筆 dataset items 的評估。"}
