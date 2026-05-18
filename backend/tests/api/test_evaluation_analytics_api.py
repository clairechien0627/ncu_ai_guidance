import json
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import api.traces as traces_api
from db import Dataset, DatasetItem, EvaluationRun, Score
from db.session import Base


def _session_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)


def _seed(db):
    db.add(Dataset(
        dataset_id="dataset-1",
        name="regression",
        source="test",
        is_archived=False,
        created_at=datetime(2026, 5, 18, 1, 0, 0),
    ))
    db.add(DatasetItem(
        dataset_item_id="item-1",
        dataset_id="dataset-1",
        source_trace_id="trace-1",
        is_archived=False,
        created_at=datetime(2026, 5, 18, 1, 1, 0),
    ))
    db.add(EvaluationRun(
        eval_run_id="eval-1",
        name="dataset-evaluation",
        status="completed",
        scope="dataset",
        dataset_id="dataset-1",
        dataset_item_count=1,
        total_count=1,
        succeeded_count=1,
        created_at=datetime(2026, 5, 18, 2, 0, 0),
    ))
    db.add(EvaluationRun(
        eval_run_id="eval-2",
        name="dataset-evaluation",
        status="completed",
        scope="dataset",
        dataset_id="dataset-1",
        dataset_item_count=1,
        total_count=1,
        succeeded_count=1,
        created_at=datetime(2026, 5, 18, 2, 1, 0),
    ))
    for eval_run_id, value in [("eval-1", 3.0), ("eval-2", 4.0)]:
        db.add(Score(
            score_id=f"{eval_run_id}:overall",
            trace_id="trace-1",
            name="overall",
            value=value,
            data_type="NUMERIC",
            source="EVAL",
            metadata_json=json.dumps({
                "eval_run_id": eval_run_id,
                "eval_item_id": f"{eval_run_id}:item-1",
                "dataset_id": "dataset-1",
                "dataset_item_id": "item-1",
            }),
            execution_trace_id=eval_run_id,
            created_at=datetime(2026, 5, 18, 3, 0, 0),
        ))
        db.add(Score(
            score_id=f"{eval_run_id}:grounding",
            trace_id="trace-1",
            name="grounding",
            value=value + 1,
            data_type="NUMERIC",
            source="EVAL",
            metadata_json=json.dumps({
                "eval_run_id": eval_run_id,
                "eval_item_id": f"{eval_run_id}:item-1",
                "dataset_id": "dataset-1",
                "dataset_item_id": "item-1",
            }),
            execution_trace_id=eval_run_id,
            created_at=datetime(2026, 5, 18, 3, 0, 0),
        ))
    db.commit()


def test_analytics_api_returns_stats_compare_and_history():
    SessionLocal = _session_factory()
    db = SessionLocal()
    try:
        _seed(db)

        run_stats = traces_api.get_evaluation_run_score_stats("eval-1", db=db)
        dataset_stats = traces_api.get_dataset_score_stats("dataset-1", db=db)
        runs = traces_api.get_dataset_eval_runs("dataset-1", db=db)
        history = traces_api.get_dataset_item_scores("dataset-1", "item-1", db=db)
        compare = traces_api.compare_evaluation_runs("eval-1", "eval-2", db=db)
        regression_cases = traces_api.get_dataset_regression_cases("dataset-1", threshold=3.5, db=db)

        assert run_stats["total"] == 1
        assert run_stats["avg_score"] == 3.0
        assert dataset_stats["avg_score"] == 3.5
        assert runs["total"] == 2
        assert runs["runs"][0]["eval_run_id"] == "eval-2"
        assert history["total"] == 2
        assert compare["delta"]["avg_score"] == 1.0
        assert regression_cases["items"][0]["dataset_item_id"] == "item-1"
    finally:
        db.close()


def test_analytics_api_missing_entities_return_404():
    SessionLocal = _session_factory()
    db = SessionLocal()
    try:
        for call in [
            lambda: traces_api.get_evaluation_run_score_stats("missing", db=db),
            lambda: traces_api.get_dataset_score_stats("missing", db=db),
            lambda: traces_api.get_dataset_eval_runs("missing", db=db),
            lambda: traces_api.get_dataset_item_scores("missing", "item", db=db),
            lambda: traces_api.get_dataset_regression_cases("missing", db=db),
            lambda: traces_api.compare_evaluation_runs("missing-left", "missing-right", db=db),
        ]:
            try:
                call()
            except HTTPException as exc:
                assert exc.status_code == 404
            else:
                raise AssertionError("expected HTTPException")
    finally:
        db.close()
