import json
from datetime import datetime

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db import Observation, Score, TraceV2
from db.session import Base
from services.trace_read.core import _TraceReadCore
from services.trace_read.detail import TraceDetailReadService
from services.trace_read.payload import TracePayloadReadService
from services.trace_read.sessions import TraceSessionUserReadService
from services.trace_read.stats import TraceStatsReadService
from services.trace_read.service import TraceReadService


def _session_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)


def _trace_v2(trace_id: str, **kwargs) -> TraceV2:
    now = kwargs.pop("start_time", datetime(2026, 5, 18, 1, 0, 0))
    meta = kwargs.pop("metadata_json", {
        "agent_name": "retrieval",
        "prompt_name": "retrieval",
        "prompt_version": "v1",
        "display": {"answer": "answer", "sources": ["source-1"]},
    })
    return TraceV2(
        trace_id=trace_id,
        name=kwargs.pop("name", "root"),
        thread_id=kwargs.pop("thread_id", "thread-v2"),
        user_id=kwargs.pop("user_id", "user-v2"),
        environment=kwargs.pop("environment", "test"),
        input=kwargs.pop("input", json.dumps({"messages": [{"role": "human", "content": "question"}]})),
        output=kwargs.pop("output", json.dumps({"answer": "answer"})),
        metadata_json=json.dumps(meta, ensure_ascii=False) if isinstance(meta, dict) else meta,
        start_time=now,
        end_time=kwargs.pop("end_time", datetime(2026, 5, 18, 1, 0, 2)),
        **kwargs,
    )


def test_v2_trace_detail_returns_children_and_scores():
    SessionLocal = _session_factory()
    db = SessionLocal()
    try:
        db.add(_trace_v2("v2-1", bookmarked=True))
        db.add(Observation(
            observation_id="obs-1",
            trace_id="v2-1",
            type="GENERATION",
            name="LLM",
            environment="test",
            prompt_id="prompt-1",
            prompt_tokens=7,
            completion_tokens=3,
            prompt_name="retrieval",
            prompt_version="v1",
            tool_calls=json.dumps([{"name": "search"}]),
            tool_definitions=json.dumps([{"name": "search"}]),
            tool_call_names=json.dumps(["search"]),
            start_time=datetime(2026, 5, 18, 1, 0, 0),
            end_time=datetime(2026, 5, 18, 1, 0, 1),
        ))
        db.add(Score(score_id="score-1", trace_id="v2-1", name="overall", value=4.0))
        db.commit()

        detail = TraceReadService(db).trace_detail("v2-1")

        assert detail["id"] == "v2-1"
        assert detail["bookmarked"] is True
        assert detail["quality_score"] == 4.0
        assert detail["prompt_tokens"] == 7
        assert detail["completion_tokens"] == 3
        assert detail["total_tokens"] == 10
        assert detail["status"] == "DEFAULT"
        assert detail["tool_count"] == 0
        assert detail["llm_call_count"] == 1
        assert "display" not in detail
        assert "run_type" not in detail
        assert "prompt_name" not in detail
        assert "prompt_version" not in detail
        assert detail["metadata"] is None
        assert detail["children"][0]["id"] == "obs-1"
        assert detail["children"][0]["trace_id"] == "v2-1"
        assert detail["children"][0]["type"] == "GENERATION"
        assert detail["children"][0]["environment"] == "test"
        assert detail["children"][0]["prompt_id"] == "prompt-1"
        assert detail["children"][0]["tool_calls"] == [{"name": "search"}]
        assert detail["children"][0]["tool_definitions"] == [{"name": "search"}]
        assert detail["children"][0]["tool_call_names"] == ["search"]
        assert "run_type" not in detail["children"][0]
        assert TraceReadService(db).list_traces(bookmarked=True)[0]["id"] == "v2-1"
        assert TraceReadService(db).list_traces(bookmarked=False) == []
        assert TraceReadService(db).observations(obs_type="GENERATION")[0]["id"] == "obs-1"
        assert TraceReadService(db).observations(obs_type="TOOL") == []
    finally:
        db.close()


def test_v2_list_filters_document_errors_slow_runs_and_compare():
    SessionLocal = _session_factory()
    db = SessionLocal()
    try:
        db.add(_trace_v2(
            "doc-v2",
            name="doc trace",
            thread_id="_extract_1",
            metadata_json={
                "document_ids": [9],
                "agent_name": "retrieval",
                "prompt_name": "retrieval",
                "prompt_version": "v2",
                "display": {"answer": "doc answer"},
            },
            start_time=datetime(2026, 5, 18, 2, 0, 0),
            end_time=datetime(2026, 5, 18, 2, 0, 12),
        ))
        db.add(_trace_v2(
            "v1-ok",
            metadata_json={"agent_name": "retrieval", "prompt_name": "retrieval", "prompt_version": "v1"},
            start_time=datetime(2026, 5, 18, 1, 0, 0),
        ))
        db.add(Observation(
            observation_id="obs-error",
            trace_id="doc-v2",
            type="TOOL",
            name="search",
            prompt_name="retrieval",
            prompt_version="v2",
            status="ERROR",
            status_message="boom",
            start_time=datetime(2026, 5, 18, 2, 0, 1),
            end_time=datetime(2026, 5, 18, 2, 0, 2),
        ))
        db.add(Score(score_id="doc-score", trace_id="doc-v2", name="overall", value=4.0))
        db.add(Observation(
            observation_id="obs-v1",
            trace_id="v1-ok",
            type="GENERATION",
            name="LLM",
            prompt_name="retrieval",
            prompt_version="v1",
            start_time=datetime(2026, 5, 18, 1, 0, 1),
        ))
        db.add(Score(score_id="v1-score", trace_id="v1-ok", name="overall", value=3.0))
        db.commit()

        service = TraceReadService(db)
        assert service.document_traces(9)[0]["id"] == "doc-v2"
        assert service.errors()[0]["id"] == "doc-v2"
        assert service.list_traces(status="ERROR")[0]["id"] == "doc-v2"
        assert service.slow_runs(min_latency=10)[0]["id"] == "doc-v2"
        compare = service.compare_prompt_versions("v1", "v2")
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


def test_split_services_match_facade_shape():
    SessionLocal = _session_factory()
    db = SessionLocal()
    try:
        db.add(_trace_v2("v2-session"))
        db.add(Observation(
            observation_id="obs-session",
            trace_id="v2-session",
            type="TOOL",
            name="search",
            start_time=datetime(2026, 5, 18, 1, 0, 0),
            end_time=datetime(2026, 5, 18, 1, 0, 1),
        ))
        db.add(Score(score_id="score-session", trace_id="v2-session", name="overall", value=4.0))
        db.commit()

        core = _TraceReadCore(db)
        detail = TraceDetailReadService(core).trace_detail("v2-session")
        payloads = TracePayloadReadService(db, core).merged_payloads({})
        stats = TraceStatsReadService(core).score_stats()
        sessions = TraceSessionUserReadService(core).sessions()
        users = TraceSessionUserReadService(core).users()

        assert detail["children"][0]["id"] == "obs-session"
        assert payloads[0]["id"] == "v2-session"
        assert stats["avg_score"] == 4.0
        assert sessions["sessions"][0]["thread_id"] == "thread-v2"
        assert users["users"][0]["user_id"] == "user-v2"
    finally:
        db.close()
