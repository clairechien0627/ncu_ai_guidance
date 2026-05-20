import json
from contextlib import contextmanager
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db import Dataset, DatasetItem, EvaluationRun, EvaluationRunItem, Score, Trace
from db.session import Base
from services.evaluation import runs as evaluation_runs
from services.datasets import DatasetService
from services.evaluation.runs import EvaluationRunService


def _session_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)


def _patch_db_session(monkeypatch, SessionLocal):
    @contextmanager
    def _db_session():
        db = SessionLocal()
        try:
            yield db
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    monkeypatch.setattr(evaluation_runs, "db_session", _db_session)


def _trace(observation_id: str, **kwargs) -> Trace:
    now = kwargs.pop("start_time", datetime(2026, 5, 18, 1, 0, 0))
    return Trace(
        observation_id=observation_id,
        trace_id=kwargs.pop("trace_id", None),
        run_type=kwargs.pop("run_type", "chain"),
        name=kwargs.pop("name", observation_id),
        start_time=now,
        end_time=kwargs.pop("end_time", now + timedelta(seconds=2)),
        agent_name=kwargs.pop("agent_name", "retrieval_agent"),
        thread_id=kwargs.pop("thread_id", "thread-1"),
        environment=kwargs.pop("environment", "test"),
        task_type=kwargs.pop("task_type", "retrieval_qa"),
        quality_score=kwargs.pop("quality_score", None),
        inputs=kwargs.pop("inputs", json.dumps({"messages": [{"role": "human", "content": "question"}]})),
        outputs=kwargs.pop("outputs", json.dumps({"answer": "answer"})),
        display=kwargs.pop("display", json.dumps({
            "answer": "answer",
            "messages": [{"role": "human", "content": "question"}],
            "sources": ["source-1"],
            "trace_summary": {"coverage": []},
        })),
        **kwargs,
    )


async def _successful_evaluator(**_kwargs):
    return {
        "grounding": 4.0,
        "task_fit": 4.0,
        "completeness": 4.0,
        "specificity": 4.0,
        "source_quality": 4.0,
        "uncertainty_honesty": 4.0,
        "format_fit": 4.0,
        "overall": 4.0,
        "verdict": "可用",
    }


def test_create_dataset_and_add_trace_item_idempotently():
    SessionLocal = _session_factory()
    db = SessionLocal()
    try:
        db.add(_trace("t1"))
        db.commit()

        dataset = DatasetService.create_dataset(db, name="regression", description="cases")
        first = DatasetService.add_trace_item(db, dataset.dataset_id, "t1")
        second = DatasetService.add_trace_item(db, dataset.dataset_id, "t1")
        detail = DatasetService.get_dataset_detail(db, dataset.dataset_id)

        assert first.dataset_item_id == second.dataset_item_id
        assert db.query(DatasetItem).count() == 1
        assert detail["item_count"] == 1
        assert detail["items"][0]["source_trace_id"] == "t1"
        assert detail["items"][0]["expected_output"] == "answer"
    finally:
        db.close()


def test_add_low_quality_traces_uses_trace_read_service_filters():
    SessionLocal = _session_factory()
    db = SessionLocal()
    try:
        db.add(_trace("low", quality_score=2.0))
        db.add(_trace("high", quality_score=4.0))
        db.commit()
        dataset = DatasetService.create_dataset(db, name="low-quality")

        result = DatasetService.add_low_quality_traces(db, dataset.dataset_id, max_quality=3.0, limit=10)

        assert result["added"] == 1
        assert result["items"][0]["source_trace_id"] == "low"
    finally:
        db.close()


def test_dataset_eval_run_scores_snapshot_without_overwriting_trace(monkeypatch):
    SessionLocal = _session_factory()
    _patch_db_session(monkeypatch, SessionLocal)
    db = SessionLocal()
    try:
        db.add(_trace("t1", quality_score=None))
        db.commit()
        dataset = DatasetService.create_dataset(db, name="regression")
        DatasetService.add_trace_item(db, dataset.dataset_id, "t1")
        run = EvaluationRunService.create_dataset_run(db, dataset_id=dataset.dataset_id)
        eval_run_id = run.eval_run_id
    finally:
        db.close()

    import asyncio
    asyncio.run(EvaluationRunService.process_run(eval_run_id, evaluator=_successful_evaluator))

    db = SessionLocal()
    try:
        run = db.query(EvaluationRun).filter(EvaluationRun.eval_run_id == eval_run_id).one()
        item = db.query(EvaluationRunItem).filter(EvaluationRunItem.eval_run_id == eval_run_id).one()
        trace = db.query(Trace).filter(Trace.observation_id == "t1").one()
        score = db.query(Score).filter(Score.execution_trace_id == eval_run_id, Score.name == "overall").one()
        metadata = json.loads(score.metadata_json)

        assert run.scope == "dataset"
        assert run.status == "completed"
        assert run.dataset_item_count == 1
        assert item.dataset_item_id
        assert item.status == "completed"
        assert trace.quality_score is None
        assert metadata["dataset_id"] == run.dataset_id
        assert metadata["dataset_item_id"] == item.dataset_item_id
    finally:
        db.close()


def test_dataset_eval_run_empty_dataset_returns_none():
    SessionLocal = _session_factory()
    db = SessionLocal()
    try:
        dataset = DatasetService.create_dataset(db, name="empty")
        run = EvaluationRunService.create_dataset_run(db, dataset_id=dataset.dataset_id)

        assert run is None
    finally:
        db.close()
