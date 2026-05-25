import json
from datetime import datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.traces import TraceBookmarkRequest, TraceFeedbackRequest, update_trace_bookmark, update_trace_feedback
from db import Observation, Score, TraceV2
from db.session import Base
from services.trace_read.service import TraceReadService


def _session_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)


def _trace(trace_id: str) -> TraceV2:
    now = datetime(2026, 5, 2, 1, 0)
    return TraceV2(
        trace_id=trace_id,
        name="router_agent",
        thread_id="thread-1",
        environment="test",
        input=json.dumps({"messages": [{"role": "user", "content": "hello"}]}),
        output=json.dumps({"answer": "world"}),
        metadata_json=json.dumps({
            "agent_name": "retrieval",
            "prompt_name": "chat",
            "prompt_version": "sha256:abc123",
            "document_ids": [1, 2],
            "display": {"answer": "legacy"},
            "tool_count": 99,
            "llm_call_count": 99,
        }),
        start_time=now,
        end_time=now + timedelta(seconds=2),
    )


def test_trace_read_payload_preserves_v2_metadata():
    SessionLocal = _session_factory()
    db = SessionLocal()
    try:
        db.add(_trace("run-1"))
        db.add(Observation(
            observation_id="obs-warning",
            trace_id="run-1",
            type="SPAN",
            name="warning",
            prompt_name="chat",
            prompt_version="sha256:abc123",
            status="WARNING",
            start_time=datetime(2026, 5, 2, 1, 0),
        ))
        db.commit()

        payload = TraceReadService(db).trace_detail("run-1")

        assert payload["id"] == "run-1"
        assert payload["latency"] == 2
        assert payload["input"] == "hello"
        assert payload["output"] == "world"
        assert payload["agent_name"] == "retrieval"
        assert payload["document_ids"] == [1, 2]
        assert payload["status"] == "WARNING"
        assert payload["obs_status_counts"] == {"WARNING": 1}
        assert payload["tool_count"] == 0
        assert payload["llm_call_count"] == 0
        assert "display" not in payload
        assert "run_type" not in payload
        assert "prompt_name" not in payload
        assert "prompt_version" not in payload
        assert payload["metadata"] is None
        assert TraceReadService(db).list_traces(status="WARNING")[0]["id"] == "run-1"
        assert TraceReadService(db).list_traces(prompt_name="chat")[0]["id"] == "run-1"
    finally:
        db.close()


def test_update_trace_feedback_validates_range():
    SessionLocal = _session_factory()
    db = SessionLocal()
    try:
        db.add(_trace("run-1"))
        db.commit()

        with pytest.raises(HTTPException) as exc:
            update_trace_feedback("run-1", TraceFeedbackRequest(quality_score=6), db=db)

        assert exc.value.status_code == 400
        assert db.query(Score).count() == 0
    finally:
        db.close()


def test_update_trace_feedback_persists_scores_and_trimmed_text():
    SessionLocal = _session_factory()
    db = SessionLocal()
    try:
        db.add(_trace("run-1"))
        db.commit()

        payload = update_trace_feedback(
            "run-1",
            TraceFeedbackRequest(quality_score=4.5, user_feedback="  good answer  "),
            db=db,
        )

        assert payload["quality_score"] == 4.5
        assert payload["user_feedback"] == "good answer"
        assert db.query(Score).filter(Score.trace_id == "run-1").count() == 2
        overall = db.query(Score).filter(Score.name == "overall").one()
        feedback = db.query(Score).filter(Score.name == "feedback").one()
        assert overall.value == 4.5
        assert overall.environment == "test"
        assert overall.thread_id == "thread-1"
        assert feedback.string_value == "good answer"
        assert feedback.environment == "test"
        assert feedback.thread_id == "thread-1"
    finally:
        db.close()


def test_update_trace_bookmark_persists_and_returns_detail():
    SessionLocal = _session_factory()
    db = SessionLocal()
    try:
        db.add(_trace("run-1"))
        db.commit()

        payload = update_trace_bookmark("run-1", TraceBookmarkRequest(bookmarked=True), db=db)

        assert payload["bookmarked"] is True
        assert db.query(TraceV2).filter(TraceV2.trace_id == "run-1").one().bookmarked is True
    finally:
        db.close()
