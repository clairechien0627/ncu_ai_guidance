import asyncio
import json
from contextlib import contextmanager
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db import Dataset, DatasetItem, EvaluationRun, ExperimentRun, ExperimentRunItem, Score
from db.session import Base
from services.evaluation import experiments
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

    monkeypatch.setattr(experiments, "db_session", _db_session)


def _seed_dataset(db, dataset_id: str = "dataset-1", item_count: int = 2):
    db.add(Dataset(
        dataset_id=dataset_id,
        name="regression",
        source="test",
        is_archived=False,
        created_at=datetime(2026, 5, 18, 1, 0, 0),
    ))
    for idx in range(item_count):
        db.add(DatasetItem(
            dataset_item_id=f"item-{idx + 1}",
            dataset_id=dataset_id,
            source_trace_id=f"trace-{idx + 1}",
            input=json.dumps({"messages": [{"role": "human", "content": f"question {idx + 1}"}]}),
            output=json.dumps({"answer": f"old answer {idx + 1}"}),
            expected_output=json.dumps({"answer": f"expected {idx + 1}"}),
            context=json.dumps({"task_type": "retrieval_qa", "route_intent": "retrieval"}),
            is_archived=False,
            created_at=datetime(2026, 5, 18, 1, 1, 0),
        ))
    db.commit()


async def _successful_generator(**kwargs):
    return {
        "output": {"answer": f"new answer for {kwargs['dataset_item_id']}"},
        "context": {
            "sources": ["source-1"],
            "task_type": "retrieval_qa",
            "route_intent": "retrieval",
        },
        "trace_id": f"generated-{kwargs['dataset_item_id']}",
    }


async def _failing_generator(**_kwargs):
    raise RuntimeError("generation failed")


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


def test_create_dataset_replay_creates_run_and_items(monkeypatch):
    SessionLocal = _session_factory()
    _patch_db_session(monkeypatch, SessionLocal)
    db = SessionLocal()
    try:
        _seed_dataset(db)

        run = ExperimentRunService.create_dataset_replay(db, dataset_id="dataset-1")

        assert run.status == "pending"
        assert run.total_count == 2
        assert run.target_agent == "router_agent_current"
        assert db.query(ExperimentRunItem).filter(ExperimentRunItem.experiment_run_id == run.experiment_run_id).count() == 2
    finally:
        db.close()


def test_create_dataset_replay_rejects_unsupported_model_override(monkeypatch):
    SessionLocal = _session_factory()
    _patch_db_session(monkeypatch, SessionLocal)
    db = SessionLocal()
    try:
        _seed_dataset(db)

        try:
            ExperimentRunService.create_dataset_replay(db, dataset_id="dataset-1", model="gpt-test")
        except HTTPException as exc:
            assert exc.status_code == 400
            assert "model" in exc.detail
        else:
            raise AssertionError("expected HTTPException")
    finally:
        db.close()


def test_process_run_saves_generated_outputs(monkeypatch):
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
        run = db.query(ExperimentRun).filter(ExperimentRun.experiment_run_id == experiment_run_id).one()
        items = db.query(ExperimentRunItem).filter(ExperimentRunItem.experiment_run_id == experiment_run_id).all()
        assert run.status == "completed"
        assert run.succeeded_count == 2
        assert all(item.status == "completed" for item in items)
        assert json.loads(items[0].generated_output)["answer"].startswith("new answer")
        assert items[0].trace_id.startswith("generated-")
    finally:
        db.close()


def test_process_run_records_failed_items(monkeypatch):
    SessionLocal = _session_factory()
    _patch_db_session(monkeypatch, SessionLocal)
    db = SessionLocal()
    try:
        _seed_dataset(db, item_count=1)
        run = ExperimentRunService.create_dataset_replay(db, dataset_id="dataset-1")
        experiment_run_id = run.experiment_run_id
    finally:
        db.close()

    asyncio.run(ExperimentRunService.process_run(experiment_run_id, generator=_failing_generator))

    db = SessionLocal()
    try:
        run = db.query(ExperimentRun).filter(ExperimentRun.experiment_run_id == experiment_run_id).one()
        item = db.query(ExperimentRunItem).filter(ExperimentRunItem.experiment_run_id == experiment_run_id).one()
        assert run.status == "failed"
        assert item.status == "failed"
        assert "generation failed" in item.error
    finally:
        db.close()


def test_experiment_eval_writes_scores_and_is_idempotent(monkeypatch):
    SessionLocal = _session_factory()
    _patch_db_session(monkeypatch, SessionLocal)
    db = SessionLocal()
    try:
        _seed_dataset(db, item_count=1)
        run = ExperimentRunService.create_dataset_replay(db, dataset_id="dataset-1")
        experiment_run_id = run.experiment_run_id
    finally:
        db.close()

    asyncio.run(ExperimentRunService.process_run(experiment_run_id, generator=_successful_generator))

    db = SessionLocal()
    try:
        eval_run = ExperimentRunService.create_eval_run_for_experiment(db, experiment_run_id)
        second = ExperimentRunService.create_eval_run_for_experiment(db, experiment_run_id)
        assert eval_run.eval_run_id == second.eval_run_id
        eval_run_id = eval_run.eval_run_id
    finally:
        db.close()

    asyncio.run(ExperimentRunService.process_experiment_eval(eval_run_id, evaluator=_successful_evaluator))
    asyncio.run(ExperimentRunService.process_experiment_eval(eval_run_id, evaluator=_successful_evaluator))

    db = SessionLocal()
    try:
        eval_run = db.query(EvaluationRun).filter(EvaluationRun.eval_run_id == eval_run_id).one()
        experiment_item = db.query(ExperimentRunItem).filter(ExperimentRunItem.experiment_run_id == experiment_run_id).one()
        scores = db.query(Score).filter(Score.execution_trace_id == eval_run_id).all()
        assert eval_run.scope == "experiment"
        assert eval_run.status == "completed"
        assert experiment_item.eval_run_id == eval_run_id
        assert len(scores) == 9
        overall = next(score for score in scores if score.name == "overall")
        metadata = json.loads(overall.metadata_json)
        assert metadata["experiment_run_id"] == experiment_run_id
        assert metadata["experiment_item_id"] == experiment_item.experiment_item_id
        assert overall.eval_run_id == eval_run_id
        assert overall.dataset_id == "dataset-1"
        assert overall.dataset_item_id == "item-1"
        assert overall.experiment_run_id == experiment_run_id
        assert overall.experiment_item_id == experiment_item.experiment_item_id
    finally:
        db.close()


def test_compare_runs_uses_strict_metadata_and_right_minus_left_delta(monkeypatch):
    SessionLocal = _session_factory()
    _patch_db_session(monkeypatch, SessionLocal)
    db = SessionLocal()
    try:
        _seed_dataset(db, item_count=1)
        run_a = ExperimentRunService.create_dataset_replay(db, dataset_id="dataset-1", name="baseline")
        run_b = ExperimentRunService.create_dataset_replay(db, dataset_id="dataset-1", name="candidate")
        item_a = db.query(ExperimentRunItem).filter(ExperimentRunItem.experiment_run_id == run_a.experiment_run_id).one()
        item_b = db.query(ExperimentRunItem).filter(ExperimentRunItem.experiment_run_id == run_b.experiment_run_id).one()
        item_a.status = "completed"
        item_b.status = "completed"
        item_a.generated_output = json.dumps({"answer": "old"})
        item_b.generated_output = json.dumps({"answer": "new"})

        db.add(Score(
            score_id="a-overall",
            name="overall",
            value=3.0,
            data_type="NUMERIC",
            source="EVAL",
            dataset_item_id="item-1",
            experiment_run_id=run_a.experiment_run_id,
            metadata_json=json.dumps({
                "dataset_item_id": "item-1",
                "experiment_run_id": run_a.experiment_run_id,
            }),
        ))
        db.add(Score(
            score_id="b-overall",
            name="overall",
            value=4.0,
            data_type="NUMERIC",
            source="EVAL",
            dataset_item_id="item-1",
            experiment_run_id=run_b.experiment_run_id,
            metadata_json=json.dumps({
                "dataset_item_id": "item-1",
                "experiment_run_id": run_b.experiment_run_id,
            }),
        ))
        db.add(Score(
            score_id="noise",
            name="overall",
            value=1.0,
            data_type="NUMERIC",
            source="EVAL",
            metadata_json=json.dumps({
                "note": f"mentions {run_a.experiment_run_id} but is not linked",
                "dataset_item_id": "item-1",
            }),
        ))
        db.commit()

        comparison = ExperimentRunService.compare_runs(db, run_a.experiment_run_id, run_b.experiment_run_id)

        assert comparison["compared_item_count"] == 1
        assert comparison["improved_count"] == 1
        assert comparison["regressed_count"] == 0
        assert comparison["items"][0]["delta"] == 1.0
        assert comparison["dimension_deltas"]["overall"]["delta"] == 1.0
    finally:
        db.close()
