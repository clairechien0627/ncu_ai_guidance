import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from api.traces import (
    TraceFeedbackRequest,
    _display,
    _grouped_prompt_version_stats,
    _grouped_trace_stats,
    _matches_trace_filters,
    _trace_payload,
    update_trace_feedback,
)


def make_trace(**overrides):
    now = datetime(2026, 5, 2, 1, 0, tzinfo=timezone.utc)
    values = {
        "id": 1,
        "observation_id": "run-1",
        "trace_id": None,
        "run_type": "chain",
        "name": "chain",
        "inputs": json.dumps({"messages": [{"content": "hello"}]}),
        "outputs": json.dumps({"answer": "world"}),
        "error": None,
        "start_time": now,
        "end_time": now + timedelta(seconds=2),
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "thread_id": "thread-1",
        "document_ids": json.dumps([1, 2]),
        "task_type": "retrieval_qa",
        "route_intent": "retrieval",
        "agent_name": "retrieval_agent",
        "prompt_name": "chat",
        "prompt_version": "sha256:abc123",
        "base_prompt_name": "base_research",
        "task_prompt_name": "chat_task",
        "quality_prompt_name": None,
        "base_prompt_hash": "sha256:base",
        "task_prompt_hash": "sha256:task",
        "quality_prompt_hash": None,
        "prompt_stack_name": "chat_default",
        "prompt_stack_json": json.dumps([
            {"name": "core", "version": "sha256:core"},
            {"name": "chat_mode", "version": "sha256:task"},
        ]),
        "primary_prompt_json": json.dumps({"name": "chat_mode", "version": "sha256:task"}),
        "workflow_prompts_json": json.dumps([
            {"name": "core", "version": "sha256:core"},
            {"name": "chat_mode", "version": "sha256:task"},
        ]),
        "prompt_stack_tokens": 123,
        "tool_count": 2,
        "llm_call_count": 3,
        "quality_score": None,
        "user_feedback": None,
        "display": json.dumps({"answer": "final answer"}),
        "input_cost": None,
        "output_cost": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_display_parses_json():
    assert _display(make_trace()) == {"answer": "final answer"}
    assert _display(make_trace(display="not-json")) is None
    assert _display(make_trace(display=None)) is None


def test_trace_payload_preserves_additive_trace_metadata():
    payload = _trace_payload(make_trace(), include_raw=True)

    assert payload["id"] == "run-1"
    assert payload["latency"] == 2
    assert payload["input"] == "hello"
    assert payload["output"] == "final answer"
    assert payload["task_type"] == "retrieval_qa"
    assert payload["route_intent"] == "retrieval"
    assert payload["agent_name"] == "retrieval_agent"
    assert payload["prompt_name"] == "chat"
    assert payload["prompt_version"] == "sha256:abc123"
    assert payload["base_prompt_name"] == "base_research"
    assert payload["task_prompt_name"] == "chat_task"
    assert payload["base_prompt_hash"] == "sha256:base"
    assert payload["prompt_stack_name"] == "chat_default"
    assert payload["prompt_stack_json"][0]["name"] == "core"
    assert payload["primary_prompt_json"]["name"] == "chat_mode"
    assert payload["workflow_prompts_json"][1]["name"] == "chat_mode"
    assert payload["prompt_stack_tokens"] == 123
    assert payload["tool_count"] == 2
    assert payload["llm_call_count"] == 3
    assert payload["document_ids"] == [1, 2]


def test_grouped_trace_stats_keeps_unknown_bucket():
    rows = _grouped_trace_stats(
        [
            make_trace(observation_id="a", route_intent="retrieval", error=None, quality_score=4, user_feedback="useful"),
            make_trace(observation_id="b", route_intent="retrieval", error="boom", end_time=None, quality_score=2),
            make_trace(observation_id="c", route_intent=None, prompt_tokens=None, completion_tokens=None),
        ],
        "route_intent",
    )

    assert rows[0]["key"] == "retrieval"
    assert rows[0]["runs"] == 2
    assert rows[0]["errors"] == 1
    assert rows[0]["tokens"] == 30
    assert rows[0]["avg_quality_score"] == 3
    assert rows[0]["feedback_count"] == 1
    assert rows[1]["key"] == "unknown"


def test_grouped_prompt_version_stats_separates_versions():
    rows = _grouped_prompt_version_stats(
        [
            make_trace(observation_id="a", prompt_name="research_writer", prompt_version="sha256:a", quality_score=5),
            make_trace(observation_id="b", prompt_name="research_writer", prompt_version="sha256:b", error="boom", quality_score=2),
            make_trace(observation_id="c", prompt_name="research_writer", prompt_version="sha256:a", quality_score=3, user_feedback="ok"),
        ],
    )

    assert rows[0]["key"] == "research_writer@sha256:a"
    assert rows[0]["runs"] == 2
    assert rows[0]["errors"] == 0
    assert rows[0]["avg_quality_score"] == 4
    assert rows[0]["feedback_count"] == 1
    assert rows[1]["key"] == "research_writer@sha256:b"
    assert rows[1]["runs"] == 1
    assert rows[1]["errors"] == 1


def test_trace_filters_match_metadata_status_and_latency():
    trace = make_trace(task_type="document_extraction", prompt_name="research_writer", prompt_version="sha256:one")

    assert _matches_trace_filters(trace, task_type="document_extraction")
    assert _matches_trace_filters(trace, prompt_name="research_writer")
    assert _matches_trace_filters(trace, prompt_version="sha256:one")
    assert _matches_trace_filters(trace, status="success")
    assert _matches_trace_filters(trace, min_latency=1.5)
    assert not _matches_trace_filters(trace, task_type="retrieval_qa")
    assert not _matches_trace_filters(trace, status="error")
    assert not _matches_trace_filters(trace, min_latency=2.5)


def test_trace_filters_can_select_unknown_metadata():
    trace = make_trace(task_type=None, prompt_name=None, prompt_version=None)

    assert _matches_trace_filters(trace, task_type="unknown")
    assert _matches_trace_filters(trace, prompt_name="unknown")
    assert _matches_trace_filters(trace, prompt_version="unknown")


class FakeQuery:
    def __init__(self, trace):
        self.trace = trace

    def filter(self, *_args, **_kwargs):
        return self

    def first(self):
        return self.trace


class FakeSession:
    def __init__(self, trace):
        self.trace = trace
        self.committed = False
        self.refreshed = None

    def query(self, *_args, **_kwargs):
        return FakeQuery(self.trace)

    def commit(self):
        self.committed = True

    def refresh(self, trace):
        self.refreshed = trace


def test_update_trace_feedback_validates_range():
    db = FakeSession(make_trace())

    with pytest.raises(HTTPException) as exc:
        update_trace_feedback("run-1", TraceFeedbackRequest(quality_score=6), db=db)

    assert exc.value.status_code == 400
    assert not db.committed


def test_update_trace_feedback_persists_score_and_trimmed_text():
    trace = make_trace()
    db = FakeSession(trace)

    payload = update_trace_feedback(
        "run-1",
        TraceFeedbackRequest(quality_score=4.5, user_feedback="  good answer  "),
        db=db,
    )

    assert db.committed
    assert db.refreshed is trace
    assert trace.quality_score == 4.5
    assert trace.user_feedback == "good answer"
    assert payload["quality_score"] == 4.5
    assert payload["user_feedback"] == "good answer"
