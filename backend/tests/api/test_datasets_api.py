import asyncio
import json
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import api.traces as traces_api
from api.traces import (
    DatasetCreateRequest,
    DatasetEvalRunRequest,
    DatasetLowQualityRequest,
    DatasetManualItemRequest,
    DatasetTraceItemRequest,
)
from db import Trace
from db.session import Base


def _session_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)


def _trace(observation_id: str, quality_score: float | None = None) -> Trace:
    now = datetime(2026, 5, 18, 1, 0, 0)
    return Trace(
        observation_id=observation_id,
        run_type="chain",
        name=observation_id,
        start_time=now,
        end_time=now + timedelta(seconds=2),
        agent_name="retrieval_agent",
        environment="test",
        task_type="retrieval_qa",
        quality_score=quality_score,
        inputs=json.dumps({"messages": [{"role": "human", "content": "question"}]}),
        outputs=json.dumps({"answer": "answer"}),
        display=json.dumps({"answer": "answer"}),
    )


def test_dataset_api_create_detail_and_add_trace():
    SessionLocal = _session_factory()
    db = SessionLocal()
    try:
        db.add(_trace("t1"))
        db.commit()

        dataset = traces_api.create_dataset(DatasetCreateRequest(name="regression"), db=db)
        dataset_id = dataset["dataset_id"]
        after_add = traces_api.add_dataset_item_from_trace(
            dataset_id,
            DatasetTraceItemRequest(trace_id="t1"),
            db=db,
        )
        listed = traces_api.list_datasets(db=db)
        detail = traces_api.get_dataset(dataset_id, db=db)

        assert listed["total"] == 1
        assert after_add["item_count"] == 1
        assert detail["items"][0]["source_trace_id"] == "t1"
    finally:
        db.close()


def test_dataset_api_manual_item_create_and_soft_delete():
    SessionLocal = _session_factory()
    db = SessionLocal()
    try:
        dataset = traces_api.create_dataset(DatasetCreateRequest(name="manual"), db=db)
        dataset_id = dataset["dataset_id"]

        after_add = traces_api.add_dataset_item_manual(
            dataset_id,
            DatasetManualItemRequest(
                input={"messages": [{"role": "human", "content": "manual question"}]},
                output={"answer": "candidate"},
                expected_output={"answer": "expected"},
                context={"task_type": "manual_eval"},
                tags=["manual"],
            ),
            db=db,
        )
        item_id = after_add["items"][0]["dataset_item_id"]
        deleted = traces_api.delete_dataset_item(dataset_id, item_id, db=db)
        detail = traces_api.get_dataset(dataset_id, db=db)

        assert after_add["item_count"] == 1
        assert after_add["items"][0]["status"] == "active"
        assert after_add["items"][0]["metadata"]["source"] == "manual"
        assert deleted == {"deleted": item_id}
        assert detail["item_count"] == 0
        assert detail["items"] == []
    finally:
        db.close()


def test_dataset_api_low_quality_and_eval_run(monkeypatch):
    SessionLocal = _session_factory()
    db = SessionLocal()
    db.add(_trace("low", quality_score=2.0))
    db.add(_trace("high", quality_score=4.0))
    db.commit()

    queued = []

    async def fake_wakeup(count=1):
        queued.append(count)

    monkeypatch.setattr(traces_api.EvaluationWorker, "wakeup", fake_wakeup)

    try:
        dataset = traces_api.create_dataset(DatasetCreateRequest(name="low-quality"), db=db)
        dataset_id = dataset["dataset_id"]
        added = traces_api.add_dataset_items_from_low_quality(
            dataset_id,
            DatasetLowQualityRequest(max_quality=3.0, limit=10),
            db=db,
        )
        response = asyncio.run(traces_api.create_dataset_eval_run(
            dataset_id,
            DatasetEvalRunRequest(),
            db=db,
        ))

        assert added["added"] == 1
        assert response["queued"] == 1
        assert response["eval_run_id"]
        assert queued == [1]
    finally:
        db.close()


def test_dataset_eval_run_empty_returns_zero_queue(monkeypatch):
    SessionLocal = _session_factory()
    db = SessionLocal()
    queued = []

    async def fake_wakeup(count=1):
        queued.append(count)

    monkeypatch.setattr(traces_api.EvaluationWorker, "wakeup", fake_wakeup)

    try:
        dataset = traces_api.create_dataset(DatasetCreateRequest(name="empty"), db=db)
        response = asyncio.run(traces_api.create_dataset_eval_run(
            dataset["dataset_id"],
            DatasetEvalRunRequest(),
            db=db,
        ))

        assert response["queued"] == 0
        assert response["eval_run_id"] is None
        assert queued == []
    finally:
        db.close()
