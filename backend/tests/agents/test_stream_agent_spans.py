"""Tests: streaming agents write correct agent SPAN parent linkage.

Contract:
  Router SPAN (trace_id)
  └── Agent SPAN (observation_id, parent=trace_id)
      └── LangChain / GENERATION observations (parent=observation_id)

For streaming agents the SPAN must be written explicitly before and after
the token stream, because LocalTracer only handles LangChain-level children.
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch


# ── helpers ─────────────────────────────────────────────────────────────────

def run(coro):
    return asyncio.run(coro)


async def _collect(agen):
    items = []
    async for item in agen:
        items.append(item)
    return items


async def _fake_stream_no_tool(*args, **kwargs):
    for token in ["hello", " world"]:
        yield token


async def _fake_run_agent_stream(*args, **kwargs):
    yield ("hello", False, [])
    yield (" world", False, [])
    yield ("", True, ["src1"])


# ── chat_agent.stream ────────────────────────────────────────────────────────

def test_chat_stream_writes_agent_span_with_trace_id_parent():
    with patch("agents.chat_agent.write_agent_span") as mock_span, \
         patch("agents.chat_agent.stream_no_tool_agent", side_effect=_fake_stream_no_tool):
        from agents import chat_agent
        items = run(_collect(chat_agent.stream(
            "hello", "thread-1",
            trace_id="trace-1", observation_id="obs-1",
        )))

    assert mock_span.call_count == 2, "should write SPAN start and end"
    for c in mock_span.call_args_list:
        assert c.kwargs["observation_id"] == "obs-1"
        assert c.kwargs["parent_observation_id"] == "trace-1"

    start_call, end_call = mock_span.call_args_list
    assert end_call.kwargs.get("end_time") is not None
    assert start_call.kwargs.get("end_time") is None
    assert any(is_done for _, is_done, _ in items)


def test_chat_stream_no_span_without_trace_id():
    with patch("agents.chat_agent.write_agent_span") as mock_span, \
         patch("agents.chat_agent.stream_no_tool_agent", side_effect=_fake_stream_no_tool):
        from agents import chat_agent
        run(_collect(chat_agent.stream("hello", "thread-1")))
    mock_span.assert_not_called()


# ── question_agent.stream ────────────────────────────────────────────────────

def test_question_stream_writes_agent_span_with_trace_id_parent():
    with patch("agents.question_agent.write_agent_span") as mock_span, \
         patch("agents.question_agent.stream_no_tool_agent", side_effect=_fake_stream_no_tool):
        from agents import question_agent
        run(_collect(question_agent.stream(
            "hello", "thread-1",
            trace_id="trace-1", observation_id="obs-1",
        )))

    assert mock_span.call_count == 2
    for c in mock_span.call_args_list:
        assert c.kwargs["parent_observation_id"] == "trace-1"
        assert c.kwargs["observation_id"] == "obs-1"


def test_question_stream_no_span_without_trace_id():
    with patch("agents.question_agent.write_agent_span") as mock_span, \
         patch("agents.question_agent.stream_no_tool_agent", side_effect=_fake_stream_no_tool):
        from agents import question_agent
        run(_collect(question_agent.stream("hello", "thread-1")))
    mock_span.assert_not_called()


# ── retrieval_agent.stream ───────────────────────────────────────────────────

def test_retrieval_stream_agent_span_parent_is_trace_id_not_self():
    """Agent SPAN parent must be trace_id — self-parent is the bug being fixed."""
    with patch("agents.retrieval_agent.write_agent_span") as mock_span, \
         patch("agents.retrieval_agent._run_agent_stream", side_effect=_fake_run_agent_stream):
        from agents import retrieval_agent
        run(_collect(retrieval_agent.stream(
            "hello", "thread-1",
            trace_id="trace-1", observation_id="obs-1",
        )))

    assert mock_span.call_count == 2
    for c in mock_span.call_args_list:
        assert c.kwargs["observation_id"] == "obs-1"
        assert c.kwargs["parent_observation_id"] == "trace-1", \
            "retrieval agent SPAN parent must be trace_id, not observation_id"
        assert c.kwargs["parent_observation_id"] != c.kwargs["observation_id"], \
            "self-parent detected"


def test_retrieval_stream_langchain_runs_parent_is_observation_id():
    """_run_agent_stream must receive parent_observation_id=observation_id so
    LocalTracer hangs LangChain runs under the agent SPAN, not the trace root."""
    captured: dict = {}

    async def capture_stream(*args, **kwargs):
        captured.update(kwargs)
        yield ("tok", False, [])
        yield ("", True, [])

    with patch("agents.retrieval_agent.write_agent_span"), \
         patch("agents.retrieval_agent._run_agent_stream", side_effect=capture_stream):
        from agents import retrieval_agent
        run(_collect(retrieval_agent.stream(
            "hello", "thread-1",
            trace_id="trace-1", observation_id="obs-1",
        )))

    assert captured.get("parent_observation_id") == "obs-1", \
        "LangChain runs must hang under agent SPAN (obs-1), not trace root"


def test_retrieval_stream_no_span_without_trace_id():
    with patch("agents.retrieval_agent.write_agent_span") as mock_span, \
         patch("agents.retrieval_agent._run_agent_stream", side_effect=_fake_run_agent_stream):
        from agents import retrieval_agent
        run(_collect(retrieval_agent.stream("hello", "thread-1")))
    mock_span.assert_not_called()


# ── route_agent_stream evaluate_after ───────────────────────────────────────

def test_route_agent_stream_evaluate_after_passes_exclude_observation_id():
    """Background evaluation must receive exclude_observation_id=step.observation_id
    so it does not re-evaluate the observation currently being written.

    This test patches _run_evaluation_agent directly to capture kwargs,
    then verifies the coroutine passed to _fire_and_forget carries the
    correct exclude_observation_id (matching the primary step's observation_id).
    """
    from agents.types import AgentRoute

    fake_route = AgentRoute(
        agent_name="chat_agent",
        prompt_name="chat_mode",
        prompt_version="v1",
        evaluate_after=True,
    )

    async def fake_chat_stream(*args, **kwargs):
        yield ("hi", False, [])
        yield ("", True, [])

    eval_kwargs_captured: list[dict] = []

    async def fake_run_evaluation_agent(thread_id, **kwargs):
        eval_kwargs_captured.append({"thread_id": thread_id, **kwargs})
        from agents.types import AgentResult
        return AgentResult(
            response="ok", sources=[], task_type="evaluation",
            agent_name="evaluation_agent",
            prompt_name="eval", prompt_version="v1", observation_id="eval-obs",
        )

    # Collect coroutines from _fire_and_forget, run them separately after main loop exits
    deferred_coros: list = []

    def capture_fire_forget(coro):
        deferred_coros.append(coro)

    with patch("agents.router_agent.classify_intent", return_value=fake_route), \
         patch("agents.router_agent._write_router_trace"), \
         patch("agents.router_agent._fire_and_forget", side_effect=capture_fire_forget), \
         patch("agents.router_agent._run_evaluation_agent", side_effect=fake_run_evaluation_agent), \
         patch("agents.chat_agent.stream", side_effect=fake_chat_stream):

        from agents.router_agent import route_agent_stream
        run(_collect(route_agent_stream(
            "hello", "thread-1",
            route=fake_route,
            trace_id="trace-1",
            observation_id="obs-1",
        )))

    # Run deferred coroutines in a fresh event loop (outside the one above)
    for coro in deferred_coros:
        run(coro)

    assert len(eval_kwargs_captured) == 1, "evaluate_after should trigger exactly one evaluation"
    kwargs = eval_kwargs_captured[0]

    # THE KEY CONTRACT: exclude_observation_id must equal the primary step's observation_id
    assert kwargs.get("exclude_observation_id") == "obs-1", (
        f"exclude_observation_id must be the primary step's observation_id 'obs-1', "
        f"got {kwargs.get('exclude_observation_id')!r}"
    )
