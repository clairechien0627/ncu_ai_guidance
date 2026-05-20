import json
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db import Observation, Score, Trace, TraceV2
from db.session import Base
from scripts.traces.backfill_trace_v2 import backfill


def _session_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)


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
        user_id=kwargs.pop("user_id", "user-1"),
        environment=kwargs.pop("environment", "test"),
        route_intent=kwargs.pop("route_intent", "document_qa"),
        prompt_name=kwargs.pop("prompt_name", "retrieval"),
        prompt_version=kwargs.pop("prompt_version", "v1"),
        prompt_tokens=kwargs.pop("prompt_tokens", 10),
        completion_tokens=kwargs.pop("completion_tokens", 5),
        quality_score=kwargs.pop("quality_score", None),
        quality_detail=kwargs.pop("quality_detail", None),
        user_feedback=kwargs.pop("user_feedback", None),
        inputs=kwargs.pop("inputs", json.dumps({"messages": [{"content": "question"}]})),
        outputs=kwargs.pop("outputs", json.dumps({"answer": "answer"})),
        display=kwargs.pop("display", json.dumps({"answer": "answer"})),
        **kwargs,
    )


def test_backfill_dry_run_does_not_write_v2_tables():
    SessionLocal = _session_factory()
    db = SessionLocal()
    try:
        db.add(_trace("root-1", quality_score=4.0))
        db.add(_trace("child-1", trace_id="root-1", run_type="llm"))
        db.commit()

        summary = backfill(db, dry_run=True)

        assert summary["roots_seen"] == 1
        assert summary["traces_written"] == 1
        assert summary["observations_written"] == 1
        assert db.query(TraceV2).count() == 0
        assert db.query(Observation).count() == 0
        assert db.query(Score).count() == 0
    finally:
        db.close()


def test_backfill_writes_idempotent_trace_observation_and_scores():
    SessionLocal = _session_factory()
    db = SessionLocal()
    try:
        db.add(_trace(
            "root-1",
            quality_score=4.0,
            quality_detail=json.dumps({"grounding": 3.0, "completeness": 5.0, "verdict": "pass"}),
            user_feedback="useful",
        ))
        db.add(_trace("child-1", trace_id="root-1", run_type="llm", prompt_tokens=8, completion_tokens=2))
        db.commit()

        first = backfill(db, dry_run=False)
        second = backfill(db, dry_run=False)

        assert first["traces_written"] == 1
        assert second["traces_written"] == 1
        assert db.query(TraceV2).filter(TraceV2.trace_id == "root-1").count() == 1
        assert db.query(Observation).filter(Observation.observation_id == "child-1").count() == 1
        assert db.query(Score).filter(Score.trace_id == "root-1").count() == 5
    finally:
        db.close()


def test_backfill_filters_by_observation_id_limit_and_since():
    SessionLocal = _session_factory()
    db = SessionLocal()
    try:
        db.add(_trace("old", start_time=datetime(2026, 5, 17, 1, 0, 0)))
        db.add(_trace("new", start_time=datetime(2026, 5, 18, 1, 0, 0)))
        db.commit()

        summary = backfill(db, dry_run=False, observation_id="new", since=datetime(2026, 5, 18, 0, 0, 0), limit=1)

        assert summary["roots_seen"] == 1
        assert db.query(TraceV2).filter(TraceV2.trace_id == "new").count() == 1
        assert db.query(TraceV2).filter(TraceV2.trace_id == "old").count() == 0
    finally:
        db.close()
