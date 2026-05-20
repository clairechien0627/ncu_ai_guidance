import asyncio
import json
from contextlib import contextmanager
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db import Dataset, DatasetItem, EvaluationRun, EvaluationRunItem, ExperimentRun, ExperimentRunItem, Score, Trace
from db.session import Base
from services.evaluation import runs as evaluation_runs, worker as evaluation_worker, experiments
from services.evaluation.runs import EvaluationRunService
from services.evaluation.worker import EvaluationWorker
from services.evaluation.experiments import ExperimentRunService


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
    monkeypatch.setattr(experiments, "db_session", _db_session)
    monkeypatch.setattr(evaluation_worker, "db_session", _db_session)


def _trace(observation_id: str) -> Trace:
    now = datetime(2026, 5, 18, 1, 0, 0)
    return Trace(
        observation_id=observation_id,
        run_type="chain",
        name=observation_id,
        start_time=now,
        end_time=now + timedelta(seconds=2),
        agent_name="retrieval_agent",
        thread_id="thread-1",
        environment="test",
        task_type="retrieval_qa",
        route_intent="retrieval",
        display=json.dumps({
            "answer": "answer",
            "messages": [{"role": "human", "content": "question"}],
            "sources": ["source-1"],
        }),
    )


def _seed_dataset(db, item_count: int = 1):
    db.add(Dataset(
        dataset_id="dataset-1",
        name="regression",
        source="test",
        is_archived=False,
        created_at=datetime(2026, 5, 18, 1, 0, 0),
    ))
    for idx in range(item_count):
        db.add(DatasetItem(
            dataset_item_id=f"item-{idx + 1}",
            dataset_id="dataset-1",
            input=json.dumps({"messages": [{"role": "human", "content": f"question {idx + 1}"}]}),
            output=json.dumps({"answer": f"old {idx + 1}"}),
            expected_output=json.dumps({"answer": f"expected {idx + 1}"}),
            context=json.dumps({"task_type": "retrieval_qa", "route_intent": "retrieval"}),
            is_archived=False,
            is_deleted=False,
            created_at=datetime(2026, 5, 18, 1, 1, 0),
        ))
    db.commit()


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


async def _successful_generator(**kwargs):
    return {
        "output": {"answer": f"generated {kwargs['dataset_item_id']}"},
        "context": {"task_type": "retrieval_qa", "route_intent": "retrieval"},
        "trace_id": f"generated-{kwargs['dataset_item_id']}",
    }


def test_worker_processes_pending_trace_evaluation_run(monkeypatch):
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

    result = asyncio.run(EvaluationWorker.process_pending_once(evaluator=_successful_evaluator))

    db = SessionLocal()
    try:
        run = db.query(EvaluationRun).filter(EvaluationRun.eval_run_id == eval_run_id).one()
        assert result["evaluation_runs"] == 1
        assert run.status == "completed"
        assert db.query(Score).filter(Score.eval_run_id == eval_run_id).count() == 9
    finally:
        db.close()


def test_worker_processes_pending_experiment_replay(monkeypatch):
    SessionLocal = _session_factory()
    _patch_db_session(monkeypatch, SessionLocal)
    db = SessionLocal()
    try:
        _seed_dataset(db)
        run = ExperimentRunService.create_dataset_replay(db, dataset_id="dataset-1")
        experiment_run_id = run.experiment_run_id
    finally:
        db.close()

    result = asyncio.run(EvaluationWorker.process_pending_once(generator=_successful_generator))

    db = SessionLocal()
    try:
        run = db.query(ExperimentRun).filter(ExperimentRun.experiment_run_id == experiment_run_id).one()
        item = db.query(ExperimentRunItem).filter(ExperimentRunItem.experiment_run_id == experiment_run_id).one()
        assert result["experiment_runs"] == 1
        assert run.status == "completed"
        assert item.status == "completed"
        assert item.trace_id == "generated-item-1"
    finally:
        db.close()


def test_worker_processes_pending_experiment_eval(monkeypatch):
    SessionLocal = _session_factory()
    _patch_db_session(monkeypatch, SessionLocal)
    db = SessionLocal()
    try:
        _seed_dataset(db)
        run = ExperimentRunService.create_dataset_replay(db, dataset_id="dataset-1")
        experiment_run_id = run.experiment_run_id
    finally:
        db.close()

    asyncio.run(ExperimentRunService.process_run(experiment_run_id, generator=_successful_generator))

    db = SessionLocal()
    try:
        eval_run = ExperimentRunService.create_eval_run_for_experiment(db, experiment_run_id)
        eval_run_id = eval_run.eval_run_id
    finally:
        db.close()

    result = asyncio.run(EvaluationWorker.process_pending_once(evaluator=_successful_evaluator))

    db = SessionLocal()
    try:
        eval_run = db.query(EvaluationRun).filter(EvaluationRun.eval_run_id == eval_run_id).one()
        assert result["experiment_eval_runs"] == 1
        assert eval_run.status == "completed"
        assert db.query(Score).filter(Score.eval_run_id == eval_run_id).count() == 9
    finally:
        db.close()


def test_worker_recovers_stale_running_records(monkeypatch):
    SessionLocal = _session_factory()
    _patch_db_session(monkeypatch, SessionLocal)
    db = SessionLocal()
    old = datetime(2026, 5, 18, 1, 0, 0)
    try:
        db.add(EvaluationRun(
            eval_run_id="eval-stale",
            name="stale",
            status="running",
            scope="trace_batch",
            total_count=1,
            updated_at=old,
        ))
        db.add(EvaluationRunItem(
            eval_item_id="eval-stale:item",
            eval_run_id="eval-stale",
            trace_id="t1",
            status="running",
            updated_at=old,
        ))
        db.add(Dataset(dataset_id="dataset-1", name="d", is_archived=False))
        db.add(ExperimentRun(
            experiment_run_id="exp-stale",
            dataset_id="dataset-1",
            name="stale",
            status="running",
            total_count=1,
            updated_at=old,
        ))
        db.add(ExperimentRunItem(
            experiment_item_id="exp-stale:item",
            experiment_run_id="exp-stale",
            dataset_item_id="item-1",
            status="running",
            updated_at=old,
        ))
        db.commit()
    finally:
        db.close()

    recovered = EvaluationWorker.recover_stale_records(timeout_seconds=1)

    db = SessionLocal()
    try:
        eval_run = db.query(EvaluationRun).filter(EvaluationRun.eval_run_id == "eval-stale").one()
        eval_item = db.query(EvaluationRunItem).filter(EvaluationRunItem.eval_item_id == "eval-stale:item").one()
        exp_run = db.query(ExperimentRun).filter(ExperimentRun.experiment_run_id == "exp-stale").one()
        exp_item = db.query(ExperimentRunItem).filter(ExperimentRunItem.experiment_item_id == "exp-stale:item").one()
        assert recovered == {
            "evaluation_items": 1,
            "evaluation_runs": 1,
            "experiment_items": 1,
            "experiment_runs": 1,
        }
        assert eval_run.status == "pending"
        assert eval_item.status == "failed"
        assert exp_run.status == "pending"
        assert exp_item.status == "failed"
    finally:
        db.close()
