import json
from datetime import datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db import Dataset, DatasetItem, EvaluationRun, Score
from db.session import Base
from services.evaluation.analytics import EvaluationAnalyticsService


def _session_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)


def _dataset(dataset_id: str = "dataset-1") -> Dataset:
    return Dataset(
        dataset_id=dataset_id,
        name="regression",
        source="test",
        is_archived=False,
        created_at=datetime(2026, 5, 18, 1, 0, 0),
    )


def _item(dataset_id: str, item_id: str, trace_id: str) -> DatasetItem:
    return DatasetItem(
        dataset_item_id=item_id,
        dataset_id=dataset_id,
        source_trace_id=trace_id,
        input=json.dumps({"question": trace_id}),
        output=json.dumps({"answer": trace_id}),
        is_archived=False,
        created_at=datetime(2026, 5, 18, 1, 1, 0),
    )


def _run(eval_run_id: str, dataset_id: str | None = None, total: int = 2, created_offset: int = 0) -> EvaluationRun:
    return EvaluationRun(
        eval_run_id=eval_run_id,
        name="dataset-evaluation" if dataset_id else "trace-batch-score",
        status="completed",
        scope="dataset" if dataset_id else "trace_batch",
        dataset_id=dataset_id,
        dataset_item_count=total if dataset_id else 0,
        total_count=total,
        succeeded_count=total,
        failed_count=0,
        created_at=datetime(2026, 5, 18, 2, 0, 0) + timedelta(minutes=created_offset),
    )


def _score(
    score_id: str,
    eval_run_id: str,
    name: str,
    value: float | None,
    *,
    dataset_id: str | None = None,
    dataset_item_id: str | None = None,
    eval_item_id: str | None = None,
    string_value: str | None = None,
    comment: str | None = None,
) -> Score:
    return Score(
        score_id=score_id,
        trace_id=f"trace-{dataset_item_id or eval_item_id or eval_run_id}",
        name=name,
        value=value,
        string_value=string_value,
        data_type="CATEGORICAL" if string_value is not None else "NUMERIC",
        source="EVAL",
        comment=comment,
        metadata_json=json.dumps({
            "eval_run_id": eval_run_id,
            "eval_item_id": eval_item_id or f"{eval_run_id}:{dataset_item_id or 'trace'}",
            "dataset_id": dataset_id,
            "dataset_item_id": dataset_item_id,
        }),
        execution_trace_id=eval_run_id,
        created_at=datetime(2026, 5, 18, 3, 0, 0),
    )


def _seed_dataset_scores(db):
    db.add(_dataset("dataset-1"))
    db.add(_item("dataset-1", "item-1", "t1"))
    db.add(_item("dataset-1", "item-2", "t2"))
    db.add(_run("eval-left", "dataset-1", total=2, created_offset=0))
    db.add(_run("eval-right", "dataset-1", total=2, created_offset=1))
    db.add_all([
        _score("left-item1-overall", "eval-left", "overall", 2.0, dataset_id="dataset-1", dataset_item_id="item-1"),
        _score("left-item1-grounding", "eval-left", "grounding", 3.0, dataset_id="dataset-1", dataset_item_id="item-1"),
        _score("left-item2-overall", "eval-left", "overall", 4.0, dataset_id="dataset-1", dataset_item_id="item-2"),
        _score("left-item2-grounding", "eval-left", "grounding", 5.0, dataset_id="dataset-1", dataset_item_id="item-2"),
        _score("right-item1-overall", "eval-right", "overall", 4.0, dataset_id="dataset-1", dataset_item_id="item-1"),
        _score("right-item1-grounding", "eval-right", "grounding", 4.0, dataset_id="dataset-1", dataset_item_id="item-1"),
        _score("right-item1-verdict", "eval-right", "verdict", None, dataset_id="dataset-1", dataset_item_id="item-1", string_value="可用", comment="ok"),
        _score("right-item2-overall", "eval-right", "overall", 5.0, dataset_id="dataset-1", dataset_item_id="item-2"),
        _score("right-item2-grounding", "eval-right", "grounding", 5.0, dataset_id="dataset-1", dataset_item_id="item-2"),
        _score("other-overall", "eval-other", "overall", 1.0, dataset_id="dataset-other", dataset_item_id="other"),
    ])
    db.commit()


def test_eval_run_score_stats_aggregates_scores():
    SessionLocal = _session_factory()
    db = SessionLocal()
    try:
        _seed_dataset_scores(db)

        stats = EvaluationAnalyticsService.eval_run_score_stats(db, "eval-left")

        assert stats["eval_run_id"] == "eval-left"
        assert stats["total"] == 2
        assert stats["scored"] == 2
        assert stats["avg_score"] == 3.0
        assert stats["low_quality_count"] == 1
        assert stats["dimension_avgs"]["grounding"] == 4.0
    finally:
        db.close()


def test_dataset_score_stats_only_counts_dataset_scores():
    SessionLocal = _session_factory()
    db = SessionLocal()
    try:
        _seed_dataset_scores(db)

        stats = EvaluationAnalyticsService.dataset_score_stats(db, "dataset-1")

        assert stats["dataset_id"] == "dataset-1"
        assert stats["total"] == 2
        assert stats["scored"] == 2
        assert stats["avg_score"] == 3.75
        assert stats["eval_run_count"] == 2
    finally:
        db.close()


def test_dataset_score_stats_prefers_explicit_columns_before_metadata_fallback():
    SessionLocal = _session_factory()
    db = SessionLocal()
    try:
        db.add(_dataset("dataset-explicit"))
        db.add(_item("dataset-explicit", "item-1", "t1"))
        db.add(_run("eval-explicit", "dataset-explicit", total=1))
        db.add(Score(
            score_id="explicit-overall",
            trace_id="trace-1",
            name="overall",
            value=4.0,
            data_type="NUMERIC",
            source="EVAL",
            dataset_id="dataset-explicit",
            dataset_item_id="item-1",
            eval_run_id="eval-explicit",
        ))
        db.add(Score(
            score_id="metadata-noise",
            trace_id="trace-2",
            name="overall",
            value=1.0,
            data_type="NUMERIC",
            source="EVAL",
            metadata_json=json.dumps({
                "dataset_id": "dataset-explicit",
                "dataset_item_id": "item-1",
                "eval_run_id": "eval-explicit",
            }),
        ))
        db.commit()

        stats = EvaluationAnalyticsService.dataset_score_stats(db, "dataset-explicit")

        assert stats["avg_score"] == 4.0
        assert stats["low_quality_count"] == 0
    finally:
        db.close()


def test_compare_eval_runs_returns_right_minus_left_delta():
    SessionLocal = _session_factory()
    db = SessionLocal()
    try:
        _seed_dataset_scores(db)

        result = EvaluationAnalyticsService.compare_eval_runs(db, "eval-left", "eval-right")

        assert result["left"]["avg_score"] == 3.0
        assert result["right"]["avg_score"] == 4.5
        assert result["delta"]["avg_score"] == 1.5
        assert result["delta"]["dimension_avgs"]["grounding"] == 0.5
    finally:
        db.close()


def test_dataset_item_score_history_groups_scores_by_eval_run():
    SessionLocal = _session_factory()
    db = SessionLocal()
    try:
        _seed_dataset_scores(db)

        result = EvaluationAnalyticsService.dataset_item_score_history(db, "dataset-1", "item-1")

        assert result["total"] == 2
        assert result["history"][0]["eval_run_id"] == "eval-left"
        assert result["history"][0]["overall"] == 2.0
        assert result["history"][1]["eval_run_id"] == "eval-right"
        assert result["history"][1]["verdict"] == "可用"
        assert "right-item1-verdict" in result["history"][1]["score_ids"]
    finally:
        db.close()


def test_empty_dataset_and_eval_run_return_stable_empty_stats():
    SessionLocal = _session_factory()
    db = SessionLocal()
    try:
        db.add(_dataset("empty-dataset"))
        db.add(_run("empty-run", total=0))
        db.commit()

        run_stats = EvaluationAnalyticsService.eval_run_score_stats(db, "empty-run")
        dataset_stats = EvaluationAnalyticsService.dataset_score_stats(db, "empty-dataset")

        assert run_stats["total"] == 0
        assert run_stats["avg_score"] is None
        assert dataset_stats["total"] == 0
        assert dataset_stats["dimension_avgs"]["grounding"] is None
    finally:
        db.close()


def test_missing_entities_raise_404():
    SessionLocal = _session_factory()
    db = SessionLocal()
    try:
        for call in [
            lambda: EvaluationAnalyticsService.eval_run_score_stats(db, "missing"),
            lambda: EvaluationAnalyticsService.dataset_score_stats(db, "missing"),
            lambda: EvaluationAnalyticsService.compare_eval_runs(db, "missing-left", "missing-right"),
        ]:
            try:
                call()
            except HTTPException as exc:
                assert exc.status_code == 404
            else:
                raise AssertionError("expected HTTPException")
    finally:
        db.close()
