import asyncio
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import api.traces as traces_api
from db import Trace
from db.session import Base


def _session_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)


def _trace(observation_id: str) -> Trace:
    now = datetime(2026, 5, 18, 1, 0, 0)
    return Trace(
        observation_id=observation_id,
        run_type="chain",
        name=observation_id,
        start_time=now,
        end_time=now + timedelta(seconds=2),
        agent_name="retrieval_agent",
        environment="test",
        quality_score=None,
    )


def test_batch_score_creates_evaluation_run_and_keeps_response_shape(monkeypatch):
    SessionLocal = _session_factory()
    db = SessionLocal()
    db.add_all([_trace("t1"), _trace("t2")])
    db.commit()

    queued = []

    async def fake_wakeup(count=1):
        queued.append(count)

    monkeypatch.setattr(traces_api.EvaluationWorker, "wakeup", fake_wakeup)

    try:
        response = asyncio.run(traces_api.batch_score_traces(limit=10, db=db))
        runs = traces_api.list_evaluation_runs(db=db)
        detail = traces_api.get_evaluation_run(runs["runs"][0]["eval_run_id"], db=db)

        assert response["queued"] == 2
        assert "message" in response
        assert queued == [2]
        assert runs["total"] == 1
        assert detail["total_count"] == 2
        assert len(detail["items"]) == 2
    finally:
        db.close()


def test_batch_score_empty_queue_does_not_create_run(monkeypatch):
    SessionLocal = _session_factory()
    db = SessionLocal()

    queued = []

    async def fake_wakeup(count=1):
        queued.append(count)

    monkeypatch.setattr(traces_api.EvaluationWorker, "wakeup", fake_wakeup)

    try:
        response = asyncio.run(traces_api.batch_score_traces(limit=10, db=db))
        runs = traces_api.list_evaluation_runs(db=db)

        assert response["queued"] == 0
        assert runs["total"] == 0
        assert queued == []
    finally:
        db.close()


def test_evaluation_runs_api_filters_and_404():
    from fastapi import HTTPException
    from db import EvaluationRun

    SessionLocal = _session_factory()
    db = SessionLocal()
    try:
        db.add(EvaluationRun(
            eval_run_id="eval-1",
            name="background-evaluation",
            status="completed",
            scope="single_trace",
            total_count=0,
        ))
        db.commit()

        assert traces_api.list_evaluation_runs(status="completed", db=db)["total"] == 1
        assert traces_api.list_evaluation_runs(name="background-evaluation", db=db)["total"] == 1
        assert traces_api.list_evaluation_runs(scope="single_trace", db=db)["total"] == 1

        try:
            traces_api.get_evaluation_run("missing", db=db)
        except HTTPException as exc:
            assert exc.status_code == 404
        else:
            raise AssertionError("expected HTTPException")
    finally:
        db.close()
