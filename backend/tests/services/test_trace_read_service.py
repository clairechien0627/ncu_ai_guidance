import json
from datetime import datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db import Observation, Score, Trace, TraceV2
from db.session import Base
from services.trace_read.core import _TraceReadCore
from services.trace_read.detail import TraceDetailReadService
from services.trace_read.payload import TraceLegacyAdapter, TracePayloadReadService
from services.trace_read.sessions import TraceSessionUserReadService
from services.trace_read.stats import TraceStatsReadService
from services.trace_read.service import TraceReadService


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


def test_v2_only_trace_detail_returns_children_and_scores():
    SessionLocal = _session_factory()
    db = SessionLocal()
    try:
        db.add(TraceV2(
            trace_id="v2-1",
            name="root",
            thread_id="thread-v2",
            user_id="user-v2",
            environment="test",
            metadata_json={"route_intent": "document_qa", "prompt_name": "retrieval"},
            start_time=datetime(2026, 5, 18, 1, 0, 0),
            end_time=datetime(2026, 5, 18, 1, 0, 2),
        ))
        db.add(Observation(
            observation_id="obs-1",
            trace_id="v2-1",
            type="llm",
            name="LLM",
            prompt_tokens=7,
            completion_tokens=3,
            start_time=datetime(2026, 5, 18, 1, 0, 0),
            end_time=datetime(2026, 5, 18, 1, 0, 1),
        ))
        db.add(Score(score_id="score-1", trace_id="v2-1", name="overall", value=4.0))
        db.commit()

        detail = TraceReadService(db).trace_detail("v2-1")

        assert detail["id"] == "v2-1"
        assert detail["quality_score"] == 4.0
        assert detail["prompt_tokens"] == 7
        assert detail["completion_tokens"] == 3
        assert detail["children"][0]["id"] == "obs-1"
    finally:
        db.close()


def test_legacy_only_trace_detail_still_falls_back():
    SessionLocal = _session_factory()
    db = SessionLocal()
    try:
        root = _trace("legacy-1", quality_score=3.5)
        child = _trace("legacy-child", trace_id="legacy-1", run_type="tool", name="search")
        db.add_all([root, child])
        db.commit()

        detail = TraceReadService(db).trace_detail("legacy-1")

        assert detail["id"] == "legacy-1"
        assert detail["quality_score"] == 3.5
        assert detail["children"][0]["id"] == "legacy-child"
    finally:
        db.close()


def test_v2_root_with_legacy_children_uses_child_fallback():
    SessionLocal = _session_factory()
    db = SessionLocal()
    try:
        db.add(TraceV2(
            trace_id="mixed-1",
            name="root",
            start_time=datetime(2026, 5, 18, 1, 0, 0),
        ))
        db.add(_trace("legacy-child", trace_id="mixed-1", run_type="tool", name="search"))
        db.commit()

        detail = TraceReadService(db).trace_detail("mixed-1")

        assert detail["children"][0]["id"] == "legacy-child"
    finally:
        db.close()


def test_list_traces_merges_v2_and_legacy_without_duplicate_root():
    SessionLocal = _session_factory()
    db = SessionLocal()
    try:
        db.add(_trace("shared-1", name="legacy shared"))
        db.add(_trace("legacy-2", name="legacy only"))
        db.add(TraceV2(
            trace_id="shared-1",
            name="v2 shared",
            start_time=datetime(2026, 5, 18, 2, 0, 0),
        ))
        db.commit()

        items = TraceReadService(db).list_traces()
        ids = [item["id"] for item in items]

        assert ids.count("shared-1") == 1
        assert "legacy-2" in ids
        assert next(item for item in items if item["id"] == "shared-1")["name"] == "v2 shared"
    finally:
        db.close()


def test_score_stats_counts_v2_and_legacy_fallback():
    SessionLocal = _session_factory()
    db = SessionLocal()
    try:
        db.add(TraceV2(
            trace_id="v2-1",
            name="root",
            start_time=datetime(2026, 5, 18, 1, 0, 0),
        ))
        db.add(Score(score_id="score-1", trace_id="v2-1", name="overall", value=4.0))
        db.add(Score(score_id="score-2", trace_id="v2-1", name="grounding", value=2.0))
        db.add(_trace("legacy-1", quality_score=2.0, quality_detail=json.dumps({"grounding": 2.0})))
        db.commit()

        stats = TraceReadService(db).score_stats()

        assert stats["total"] == 2
        assert stats["scored"] == 2
        assert stats["low_quality_count"] == 1
        assert stats["dimension_avgs"]["grounding"] == 2.0
    finally:
        db.close()


def test_document_traces_errors_slow_runs_and_compare_are_v2_aware():
    SessionLocal = _session_factory()
    db = SessionLocal()
    try:
        db.add(TraceV2(
            trace_id="doc-v2",
            name="doc trace",
            thread_id="_extract_1",
            environment="test",
            metadata_json={
                "document_ids": [9],
                "route_intent": "document_qa",
                "prompt_name": "retrieval",
                "prompt_version": "v2",
                "display": {"answer": "doc answer"},
            },
            start_time=datetime(2026, 5, 18, 2, 0, 0),
            end_time=datetime(2026, 5, 18, 2, 0, 12),
        ))
        db.add(Observation(
            observation_id="obs-error",
            trace_id="doc-v2",
            type="tool",
            name="search",
            level="ERROR",
            status_message="boom",
            start_time=datetime(2026, 5, 18, 2, 0, 1),
            end_time=datetime(2026, 5, 18, 2, 0, 2),
        ))
        db.add(_trace(
            "legacy-v1",
            prompt_version="v1",
            quality_score=2.0,
            start_time=datetime(2026, 5, 18, 1, 0, 0),
        ))
        db.add(Score(score_id="doc-score", trace_id="doc-v2", name="overall", value=4.0))
        db.commit()

        service = TraceReadService(db)
        document_items = service.document_traces(9)
        errors = service.errors()
        slow = service.slow_runs(min_latency=10)
        compare = service.compare_prompt_versions("v1", "v2")

        assert document_items[0]["id"] == "doc-v2"
        assert errors[0]["id"] == "doc-v2"
        assert slow[0]["id"] == "doc-v2"
        assert compare["v1"]["runs"] == 1
        assert compare["v2"]["runs"] == 1
        assert compare["v2"]["errors"] == 1
    finally:
        db.close()


def test_missing_trace_raises_404():
    SessionLocal = _session_factory()
    db = SessionLocal()
    try:
        with pytest.raises(HTTPException):
            TraceReadService(db).trace_detail("missing")
    finally:
        db.close()


def test_split_payload_and_legacy_services_keep_dedupe_and_filters():
    SessionLocal = _session_factory()
    db = SessionLocal()
    try:
        db.add(_trace("shared-1", name="legacy shared", environment="prod"))
        db.add(_trace("legacy-error", error="boom", environment="prod", quality_score=2.0))
        db.add(TraceV2(
            trace_id="shared-1",
            name="v2 shared",
            environment="prod",
            start_time=datetime(2026, 5, 18, 2, 0, 0),
        ))
        db.commit()

        core = _TraceReadCore(db)
        payload_service = TracePayloadReadService(db, core)
        legacy = TraceLegacyAdapter(core)

        payloads = payload_service.merged_payloads({"environment": "prod"}, include_display=False)
        ids = [payload["id"] for payload in payloads]
        legacy_errors = legacy.filtered({"status": "error"}, limit=10)

        assert ids.count("shared-1") == 1
        assert "legacy-error" in ids
        assert legacy_errors[0].observation_id == "legacy-error"
    finally:
        db.close()


def test_split_detail_stats_and_session_services_match_facade_shape():
    SessionLocal = _session_factory()
    db = SessionLocal()
    try:
        db.add(TraceV2(
            trace_id="v2-session",
            name="root",
            thread_id="thread-v2",
            user_id="user-v2",
            metadata_json={"route_intent": "document_qa"},
            start_time=datetime(2026, 5, 18, 1, 0, 0),
            end_time=datetime(2026, 5, 18, 1, 0, 2),
        ))
        db.add(Observation(
            observation_id="obs-session",
            trace_id="v2-session",
            type="tool",
            name="search",
            start_time=datetime(2026, 5, 18, 1, 0, 0),
            end_time=datetime(2026, 5, 18, 1, 0, 1),
        ))
        db.add(Score(score_id="score-session", trace_id="v2-session", name="overall", value=4.0))
        db.commit()

        core = _TraceReadCore(db)
        detail = TraceDetailReadService(core).trace_detail("v2-session")
        stats = TraceStatsReadService(core).score_stats()
        sessions = TraceSessionUserReadService(core).sessions()
        users = TraceSessionUserReadService(core).users()

        assert detail["children"][0]["id"] == "obs-session"
        assert stats["avg_score"] == 4.0
        assert sessions["sessions"][0]["thread_id"] == "thread-v2"
        assert users["users"][0]["user_id"] == "user-v2"
    finally:
        db.close()
