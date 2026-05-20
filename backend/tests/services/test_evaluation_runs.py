import json
from contextlib import contextmanager
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db import EvaluationRun, EvaluationRunItem, Score, Trace
from db.session import Base
from services import evaluation_runs
from services.evaluation_runs import EvaluationRunService


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
        route_intent=kwargs.pop("route_intent", "retrieval"),
        display=kwargs.pop("display", json.dumps({
            "answer": "answer",
            "messages": [{"role": "human", "content": "question"}],
            "sources": ["source-1"],
        })),
        quality_score=kwargs.pop("quality_score", None),
        **kwargs,
    )


async def _successful_evaluator(**_kwargs):
    return {
        "grounding": 4.0,
        "task_fit": 4.0,
        "completeness": 3.0,
        "specificity": 4.0,
        "source_quality": 4.0,
        "uncertainty_honesty": 5.0,
        "format_fit": 4.0,
        "overall": 4.0,
        "verdict": "可用",
        "issues": [],
    }


async def _failing_evaluator(**_kwargs):
    raise RuntimeError("eval failed")


def test_create_trace_batch_creates_run_and_items(monkeypatch):
    SessionLocal = _session_factory()
    _patch_db_session(monkeypatch, SessionLocal)
    db = SessionLocal()
    try:
        db.add_all([_trace("t1"), _trace("t2")])
        db.commit()

        run = EvaluationRunService.create_trace_batch(db, limit=10)

        assert run.status == "pending"
        assert run.total_count == 2
        assert db.query(EvaluationRunItem).filter(EvaluationRunItem.eval_run_id == run.eval_run_id).count() == 2
    finally:
        db.close()


def test_empty_batch_returns_none_and_creates_no_run(monkeypatch):
    SessionLocal = _session_factory()
    _patch_db_session(monkeypatch, SessionLocal)
    db = SessionLocal()
    try:
        run = EvaluationRunService.create_trace_batch(db, limit=10)

        assert run is None
        assert db.query(EvaluationRun).count() == 0
    finally:
        db.close()


def test_process_run_writes_scores_and_status(monkeypatch):
    SessionLocal = _session_factory()
    _patch_db_session(monkeypatch, SessionLocal)
    db = SessionLocal()
    try:
        db.add(_trace("t1"))
        db.commit()
        run = EvaluationRunService.create_trace_batch(db, limit=10)
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

        assert run.status == "completed"
        assert run.succeeded_count == 1
        assert item.status == "completed"
        assert json.loads(item.score_ids)
        assert trace.quality_score == 4.0
        assert "overall" in json.loads(trace.quality_detail)
        assert trace.user_feedback
        assert db.query(Score).filter(Score.execution_trace_id == eval_run_id).count() == 9
        score = db.query(Score).filter(Score.execution_trace_id == eval_run_id, Score.name == "overall").one()
        metadata = json.loads(score.metadata_json)
        assert metadata["eval_run_id"] == eval_run_id
        assert metadata["eval_item_id"] == item.eval_item_id
    finally:
        db.close()


def test_process_run_records_failed_item(monkeypatch):
    SessionLocal = _session_factory()
    _patch_db_session(monkeypatch, SessionLocal)
    db = SessionLocal()
    try:
        db.add(_trace("t1"))
        db.commit()
        run = EvaluationRunService.create_trace_batch(db, limit=10)
        eval_run_id = run.eval_run_id
    finally:
        db.close()

    import asyncio
    asyncio.run(EvaluationRunService.process_run(eval_run_id, evaluator=_failing_evaluator))

    db = SessionLocal()
    try:
        run = db.query(EvaluationRun).filter(EvaluationRun.eval_run_id == eval_run_id).one()
        item = db.query(EvaluationRunItem).filter(EvaluationRunItem.eval_run_id == eval_run_id).one()

        assert run.status == "failed"
        assert run.failed_count == 1
        assert item.status == "failed"
        assert "eval failed" in item.error
    finally:
        db.close()


def test_completed_item_is_not_scored_twice(monkeypatch):
    SessionLocal = _session_factory()
    _patch_db_session(monkeypatch, SessionLocal)
    db = SessionLocal()
    try:
        db.add(_trace("t1"))
        db.commit()
        run = EvaluationRunService.create_trace_batch(db, limit=10)
        eval_run_id = run.eval_run_id
    finally:
        db.close()


def test_retry_failed_items_can_complete_after_failure(monkeypatch):
    SessionLocal = _session_factory()
    _patch_db_session(monkeypatch, SessionLocal)
    db = SessionLocal()
    try:
        db.add(_trace("t1"))
        db.commit()
        run = EvaluationRunService.create_trace_batch(db, limit=10)
        eval_run_id = run.eval_run_id
    finally:
        db.close()

    import asyncio
    asyncio.run(EvaluationRunService.process_run(eval_run_id, evaluator=_failing_evaluator))

    db = SessionLocal()
    try:
        assert EvaluationRunService.retry_failed_items(db, eval_run_id) == 1
    finally:
        db.close()

    asyncio.run(EvaluationRunService.process_run(eval_run_id, evaluator=_successful_evaluator))

    db = SessionLocal()
    try:
        run = db.query(EvaluationRun).filter(EvaluationRun.eval_run_id == eval_run_id).one()
        item = db.query(EvaluationRunItem).filter(EvaluationRunItem.eval_run_id == eval_run_id).one()
        assert run.status == "completed"
        assert item.status == "completed"
    finally:
        db.close()


def test_list_runs_filters_by_status_name_and_scope(monkeypatch):
    SessionLocal = _session_factory()
    _patch_db_session(monkeypatch, SessionLocal)
    db = SessionLocal()
    try:
        db.add(_trace("t1"))
        db.add(_trace("t2"))
        db.commit()
        first = EvaluationRunService.create_trace_batch(db, limit=1, name="trace-batch-score")
        second = EvaluationRunService.create_single_trace(
            db,
            trace_id="t2",
            name="background-evaluation",
            scope="single_trace",
        )
        first.status = "completed"
        second.status = "failed"
        db.commit()

        assert EvaluationRunService.list_runs(db, status="completed")["total"] == 1
        assert EvaluationRunService.list_runs(db, name="background-evaluation")["total"] == 1
        assert EvaluationRunService.list_runs(db, scope="single_trace")["total"] == 1
    finally:
        db.close()


def test_get_run_detail_missing_raises_404(monkeypatch):
    from fastapi import HTTPException

    SessionLocal = _session_factory()
    _patch_db_session(monkeypatch, SessionLocal)
    db = SessionLocal()
    try:
        try:
            EvaluationRunService.get_run_detail(db, "missing")
        except HTTPException as exc:
            assert exc.status_code == 404
        else:
            raise AssertionError("expected HTTPException")
    finally:
        db.close()
