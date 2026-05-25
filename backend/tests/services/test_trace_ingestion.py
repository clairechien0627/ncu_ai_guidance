from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db.session import Base
from db import Observation, Score, TraceEventOutbox, TraceV2
from services import trace_ingestion
from services.trace_ingestion import TraceEventIngestor, TraceIngestionWorker


def _session_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)


def test_outbox_duplicate_event_id_is_idempotent(monkeypatch):
    SessionLocal = _session_factory()
    monkeypatch.setattr(trace_ingestion, "SessionLocal", SessionLocal)

    event = {
        "event_id": "trace-create:t1",
        "event_type": "trace-create",
        "body": {
            "trace_id": "t1",
            "name": "root",
            "start_time": datetime(2026, 5, 18, 1, 0, 0),
        },
    }

    assert TraceEventIngestor.enqueue_sync([event, event]) == 1

    db = SessionLocal()
    try:
        assert db.query(TraceEventOutbox).count() == 1
    finally:
        db.close()


def test_worker_processes_trace_observation_and_score(monkeypatch):
    SessionLocal = _session_factory()
    monkeypatch.setattr(trace_ingestion, "SessionLocal", SessionLocal)

    events = [
        {
            "event_id": "trace-create:t1",
            "event_type": "trace-create",
                "body": {
                    "trace_id": "t1",
                    "name": "root",
                    "thread_id": "thread-1",
                    "environment": "staging",
                    "bookmarked": True,
                    "metadata": {
                    "agent_name": "chat",
                    "document_ids": [1],
                    "display": {"answer": "legacy"},
                    "tool_count": 99,
                    "prompt_name": "chat_mode",
                    "prompt_stack_json": [{"name": "chat_mode"}],
                },
                "start_time": datetime(2026, 5, 18, 1, 0, 0),
                "end_time": datetime(2026, 5, 18, 1, 0, 2),
            },
        },
        {
            "event_id": "observation-create:o1",
            "event_type": "observation-create",
                "body": {
                    "observation_id": "o1",
                    "trace_id": "t1",
                    "type": "llm",
                    "name": "LLM",
                    "environment": "staging",
                    "prompt_id": "prompt-1",
                    "tool_calls": [{"id": "call-1", "name": "search"}],
                    "tool_definitions": [{"name": "search"}],
                    "tool_call_names": ["search"],
                    "usage": {"input": 10, "output": 5, "total": 15},
                "status": "WARNING",
                "start_time": datetime(2026, 5, 18, 1, 0, 0),
                "end_time": datetime(2026, 5, 18, 1, 0, 1),
            },
        },
        {
            "event_id": "score-create:s1",
            "event_type": "score-create",
            "body": {
                "score_id": "s1",
                "trace_id": "t1",
                "name": "overall",
                "value": 4.5,
                "source": "EVAL",
            },
        },
    ]

    assert TraceEventIngestor.enqueue_sync(events) == 3
    assert TraceIngestionWorker.process_pending() == 3

    db = SessionLocal()
    try:
        assert db.query(TraceV2).filter(TraceV2.trace_id == "t1").count() == 1
        trace = db.query(TraceV2).filter(TraceV2.trace_id == "t1").one()
        assert trace.bookmarked is True
        assert trace.environment == "staging"
        assert trace.metadata_json == '{"agent_name": "chat", "document_ids": [1]}'
        obs = db.query(Observation).filter(Observation.observation_id == "o1").one()
        assert obs.environment == "staging"
        assert obs.prompt_id == "prompt-1"
        assert obs.tool_calls == '[{"id": "call-1", "name": "search"}]'
        assert obs.tool_definitions == '[{"name": "search"}]'
        assert obs.tool_call_names == '["search"]'
        assert obs.status == "WARNING"
        assert obs.total_tokens == 15
        score = db.query(Score).filter(Score.score_id == "s1").one()
        assert score.value == 4.5
        assert score.environment == "staging"
        assert score.thread_id == "thread-1"
        assert db.query(TraceEventOutbox).filter(TraceEventOutbox.status == "processed").count() == 3
    finally:
        db.close()


def test_observation_update_without_prompt_fields_preserves_capture(monkeypatch):
    SessionLocal = _session_factory()
    monkeypatch.setattr(trace_ingestion, "SessionLocal", SessionLocal)

    events = [
        {
            "event_id": "trace-create:t1",
            "event_type": "trace-create",
            "body": {"trace_id": "t1", "name": "root"},
        },
        {
            "event_id": "observation-create:o1",
            "event_type": "observation-create",
            "body": {
                "observation_id": "o1",
                "trace_id": "t1",
                "type": "GENERATION",
                "name": "LLM",
                "prompt_id": "chat_mode:sha256:abc",
                "prompt_name": "chat_mode",
                "prompt_version": "sha256:abc",
                "tool_call_names": ["lookup"],
            },
        },
        {
            "event_id": "observation-update:o1",
            "event_type": "observation-update",
            "body": {
                "observation_id": "o1",
                "trace_id": "t1",
                "usage": {"input": 1, "output": 2, "total": 3},
            },
        },
    ]

    assert TraceEventIngestor.enqueue_sync(events) == 3
    assert TraceIngestionWorker.process_pending() == 3

    db = SessionLocal()
    try:
        obs = db.query(Observation).filter(Observation.observation_id == "o1").one()
        assert obs.prompt_id == "chat_mode:sha256:abc"
        assert obs.prompt_name == "chat_mode"
        assert obs.prompt_version == "sha256:abc"
        assert obs.tool_call_names == '["lookup"]'
        assert obs.total_tokens == 3
    finally:
        db.close()


def test_worker_records_failure_for_unsupported_event(monkeypatch):
    SessionLocal = _session_factory()
    monkeypatch.setattr(trace_ingestion, "SessionLocal", SessionLocal)
    TraceEventIngestor.enqueue_sync([{
        "event_id": "bad:event",
        "event_type": "unknown",
        "body": {},
    }])

    assert TraceIngestionWorker.process_pending() == 0

    db = SessionLocal()
    try:
        row = db.query(TraceEventOutbox).filter(TraceEventOutbox.event_id == "bad:event").one()
        assert row.status == "failed"
        assert row.attempts == 1
        assert "Unsupported trace event type" in row.last_error
    finally:
        db.close()
