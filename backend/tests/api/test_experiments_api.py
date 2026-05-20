import asyncio
import json
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import api.traces as traces_api
from api.traces import ExperimentEvalRequest, ExperimentReplayRequest
from db import Dataset, DatasetItem, ExperimentRunItem, Score
from db.session import Base
from services.evaluation.experiments import ExperimentRunService


def _session_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)


def _seed_dataset(db):
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
        input=json.dumps({"messages": [{"role": "human", "content": "question"}]}),
        output=json.dumps({"answer": "old"}),
        expected_output=json.dumps({"answer": "expected"}),
        context=json.dumps({"task_type": "retrieval_qa", "agent_name": "retrieval_agent"}),
        is_archived=False,
        created_at=datetime(2026, 5, 18, 1, 1, 0),
    ))
    db.commit()


def test_experiment_api_create_list_detail_eval_and_stats(monkeypatch):
    SessionLocal = _session_factory()
    db = SessionLocal()
    queued = []

    async def fake_wakeup(count=1):
        queued.append(count)

    monkeypatch.setattr(traces_api.EvaluationWorker, "wakeup", fake_wakeup)
    try:
        _seed_dataset(db)

        replay = asyncio.run(traces_api.create_dataset_replay(
            ExperimentReplayRequest(dataset_id="dataset-1"),
            db=db,
        ))
        experiment_run_id = replay["experiment_run_id"]
        item = db.query(ExperimentRunItem).filter(ExperimentRunItem.experiment_run_id == experiment_run_id).one()
        item.status = "completed"
        item.generated_output = json.dumps({"answer": "new"})
        item.generated_context = json.dumps({"task_type": "retrieval_qa", "route_intent": "retrieval"})
        item.trace_id = "generated-trace"
        db.commit()

        runs = traces_api.list_experiment_runs(db=db)
        detail = traces_api.get_experiment_run(experiment_run_id, db=db)
        eval_response = asyncio.run(traces_api.create_experiment_eval_run(
            experiment_run_id,
            ExperimentEvalRequest(),
            db=db,
        ))
        eval_run_id = eval_response["eval_run_id"]
        item.eval_run_id = eval_run_id
        db.add(Score(
            score_id="score-1",
            trace_id="generated-trace",
            name="overall",
            value=4.0,
            data_type="NUMERIC",
            source="EVAL",
            metadata_json=json.dumps({
                "eval_run_id": eval_run_id,
                "dataset_id": "dataset-1",
                "dataset_item_id": "item-1",
                "experiment_run_id": experiment_run_id,
                "experiment_item_id": item.experiment_item_id,
            }),
            execution_trace_id=eval_run_id,
        ))
        db.commit()
        stats = traces_api.get_experiment_score_stats(experiment_run_id, db=db)
        report = traces_api.get_experiment_report(experiment_run_id, db=db)

        assert replay["queued"] == 1
        assert runs["total"] == 1
        assert detail["items"][0]["dataset_item_id"] == "item-1"
        assert eval_response["queued"] == 1
        assert stats["avg_score"] == 4.0
        assert report["worst_items"][0]["dataset_item_id"] == "item-1"
        assert queued == [1, 1]
    finally:
        db.close()


def test_experiment_compare_api_returns_candidate_delta():
    SessionLocal = _session_factory()
    db = SessionLocal()
    try:
        _seed_dataset(db)
        run_a = ExperimentRunService.create_dataset_replay(db, dataset_id="dataset-1", name="baseline")
        run_b = ExperimentRunService.create_dataset_replay(db, dataset_id="dataset-1", name="candidate")
        exp_a = run_a.experiment_run_id
        exp_b = run_b.experiment_run_id
        for exp_id, value in [(exp_a, 2.0), (exp_b, 4.0)]:
            item = db.query(ExperimentRunItem).filter(ExperimentRunItem.experiment_run_id == exp_id).one()
            item.status = "completed"
            item.generated_output = json.dumps({"answer": f"answer {value}"})
            db.add(Score(
                score_id=f"{exp_id}:overall",
                name="overall",
                value=value,
                data_type="NUMERIC",
                source="EVAL",
                dataset_item_id="item-1",
                experiment_run_id=exp_id,
                metadata_json=json.dumps({
                    "dataset_item_id": "item-1",
                    "experiment_run_id": exp_id,
                }),
            ))
        db.commit()

        comparison = traces_api.compare_experiment_runs(a=exp_a, b=exp_b, db=db)
        report = traces_api.compare_experiment_runs_report(left=exp_a, right=exp_b, db=db)

        assert comparison["compared_item_count"] == 1
        assert comparison["items"][0]["delta"] == 2.0
        assert comparison["improved_count"] == 1
        assert report["summary"]["improved_count"] == 1
    finally:
        db.close()


def test_experiment_replay_api_rejects_unsupported_override():
    SessionLocal = _session_factory()
    db = SessionLocal()
    try:
        _seed_dataset(db)

        try:
            asyncio.run(traces_api.create_dataset_replay(
                ExperimentReplayRequest(dataset_id="dataset-1", model="gpt-test"),
                db=db,
            ))
        except HTTPException as exc:
            assert exc.status_code == 400
        else:
            raise AssertionError("expected HTTPException")
    finally:
        db.close()


def test_experiment_api_missing_entities_return_404():
    SessionLocal = _session_factory()
    db = SessionLocal()
    try:
        for call in [
            lambda: asyncio.run(traces_api.create_dataset_replay(ExperimentReplayRequest(dataset_id="missing"), db=db)),
            lambda: traces_api.get_experiment_run("missing", db=db),
            lambda: traces_api.get_experiment_score_stats("missing", db=db),
            lambda: asyncio.run(traces_api.create_experiment_eval_run("missing", ExperimentEvalRequest(), db=db)),
        ]:
            try:
                call()
            except HTTPException as exc:
                assert exc.status_code == 404
            else:
                raise AssertionError("expected HTTPException")
    finally:
        db.close()
