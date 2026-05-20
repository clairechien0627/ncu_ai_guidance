import asyncio
import contextlib

import pytest

from agents.router_agent import (
    RouterDecision,
    _allows_background_evaluation,
    _build_execution_plan,
    _normalise_decision,
    classify_intent,
)
from agents.types import AgentRoute


# ── Orchestrator routes (mocked _orchestrate) ────────────────────────────────

def _fake_orchestrate(agent_name: str, evaluate_after: bool = False):
    async def _inner(*args, **kwargs):
        return RouterDecision(agent_name=agent_name, evaluate_after=evaluate_after, reason="test")
    return _inner


def test_router_routes_to_research_agent(monkeypatch):
    monkeypatch.setattr("agents.router_agent._orchestrate", _fake_orchestrate("research_agent"))
    route = asyncio.run(classify_intent("請整理研究動機、研究方法、研究成果與限制", [1]))
    assert route.agent_name == "research_agent"
    assert route.intent == "research"
    assert route.prompt_name == "research_writer"


def test_router_routes_to_question_agent(monkeypatch):
    monkeypatch.setattr("agents.router_agent._orchestrate", _fake_orchestrate("question_agent"))
    route = asyncio.run(classify_intent("請出三題導讀問題給我", [1]))
    assert route.agent_name == "question_agent"
    assert route.intent == "question"
    assert route.prompt_name == "question_skill"


def test_router_routes_to_retrieval_agent(monkeypatch):
    monkeypatch.setattr("agents.router_agent._orchestrate", _fake_orchestrate("retrieval_agent"))
    route = asyncio.run(classify_intent("這篇論文的研究方法細節？", [1]))
    assert route.agent_name == "retrieval_agent"
    assert route.intent == "retrieval"


def test_router_routes_to_chat_without_documents(monkeypatch):
    monkeypatch.setattr("agents.router_agent._orchestrate", _fake_orchestrate("chat_agent"))
    route = asyncio.run(classify_intent("我想討論一個研究方向"))
    assert route.agent_name == "chat_agent"
    assert route.intent == "chat"


def test_router_routes_meta_question_to_chat(monkeypatch):
    """Meta questions answerable from abstract should go to chat_agent."""
    monkeypatch.setattr("agents.router_agent._orchestrate", _fake_orchestrate("chat_agent"))
    route = asyncio.run(classify_intent("這是關於什麼領域的", [1]))
    assert route.agent_name == "chat_agent"
    assert route.intent == "chat"


def test_router_routes_to_evaluation_agent(monkeypatch):
    monkeypatch.setattr("agents.router_agent._orchestrate", _fake_orchestrate("evaluation_agent"))
    route = asyncio.run(classify_intent("評估一下剛才的回答好不好"))
    assert route.agent_name == "evaluation_agent"
    assert route.intent == "evaluation"
    assert route.evaluate_after is False


# ── Orchestrator always called (no keyword fast-path) ────────────────────────

def test_orchestrate_is_always_called(monkeypatch):
    """Every classify_intent call goes through _orchestrate — no keyword bypass."""
    calls = 0

    async def counting_orchestrate(*args, **kwargs):
        nonlocal calls
        calls += 1
        return RouterDecision(agent_name="chat_agent", evaluate_after=False, reason="test")

    monkeypatch.setattr("agents.router_agent._orchestrate", counting_orchestrate)
    asyncio.run(classify_intent("幫我摘要這份文件", [1]))
    assert calls == 1


def test_orchestrate_receives_document_context_when_has_docs(monkeypatch):
    """_orchestrate receives document_ids so it can fetch abstracts."""
    captured = {}

    async def spy_orchestrate(message, has_docs, document_ids=None, **kwargs):
        captured["document_ids"] = document_ids
        captured["has_docs"] = has_docs
        return RouterDecision(agent_name="retrieval_agent", evaluate_after=False, reason="test")

    monkeypatch.setattr("agents.router_agent._orchestrate", spy_orchestrate)
    asyncio.run(classify_intent("研究方法是什麼", [42, 99]))
    assert captured["has_docs"] is True
    assert captured["document_ids"] == [42, 99]


def test_orchestrate_receives_is_followup_signal(monkeypatch):
    """Short follow-up messages should have is_followup_signal=True."""
    captured = {}

    async def spy_orchestrate(message, has_docs, document_ids=None,
                               previous_agent=None, is_followup=False):
        captured["is_followup"] = is_followup
        return RouterDecision(agent_name="retrieval_agent", evaluate_after=False, reason="test")

    monkeypatch.setattr("agents.router_agent._orchestrate", spy_orchestrate)
    asyncio.run(classify_intent("還有呢", [1], previous_intent="retrieval"))
    assert captured["is_followup"] is True


def test_evaluate_after_propagates_to_route(monkeypatch):
    monkeypatch.setattr("agents.router_agent._orchestrate",
                        _fake_orchestrate("research_agent", evaluate_after=True))
    route = asyncio.run(classify_intent("幫我分析完之後確認有沒有漏掉重點", [1]))
    assert route.agent_name == "research_agent"
    assert route.evaluate_after is True


# ── RouterDecision model ──────────────────────────────────────────────────────

def test_router_decision_default_fields():
    d = RouterDecision(agent_name="chat_agent")
    assert d.evaluate_after is False
    assert d.reason == ""


def test_router_decision_all_fields():
    d = RouterDecision(agent_name="research_agent", evaluate_after=True, reason="user said so")
    assert d.agent_name == "research_agent"
    assert d.evaluate_after is True
    assert d.reason == "user said so"


def test_normalise_decision_clears_evaluate_after_for_chat():
    d = RouterDecision(agent_name="chat_agent", evaluate_after=True)
    result = _normalise_decision(d, has_docs=True)
    assert result.evaluate_after is False


def test_normalise_decision_clears_evaluate_after_for_evaluation():
    d = RouterDecision(agent_name="evaluation_agent", evaluate_after=True)
    result = _normalise_decision(d, has_docs=False)
    assert result.evaluate_after is False


def test_normalise_decision_preserves_evaluate_after_for_research():
    d = RouterDecision(agent_name="research_agent", evaluate_after=True, reason="ok")
    result = _normalise_decision(d, has_docs=True)
    assert result.evaluate_after is True


def test_normalise_decision_preserves_evaluate_after_for_question():
    d = RouterDecision(agent_name="question_agent", evaluate_after=True, reason="ok")
    result = _normalise_decision(d, has_docs=True)
    assert result.evaluate_after is True


def test_normalise_decision_preserves_evaluate_after_for_retrieval():
    d = RouterDecision(agent_name="retrieval_agent", evaluate_after=True, reason="ok")
    result = _normalise_decision(d, has_docs=True)
    assert result.evaluate_after is True


def test_normalise_decision_gates_research_on_docs():
    d = RouterDecision(agent_name="research_agent", evaluate_after=True)
    result = _normalise_decision(d, has_docs=False)
    assert result.agent_name == "chat_agent"
    assert result.evaluate_after is False


def test_normalise_decision_falls_back_for_unknown_agent():
    d = RouterDecision(agent_name="nonexistent_agent", evaluate_after=False)
    result = _normalise_decision(d, has_docs=True)
    assert result.agent_name == "chat_agent"


# ── _allows_background_evaluation ────────────────────────────────────────────

def test_background_evaluation_policy_is_document_grounded_only():
    assert _allows_background_evaluation("research_agent", has_docs=True) is True
    assert _allows_background_evaluation("retrieval_agent", has_docs=True) is True
    assert _allows_background_evaluation("question_agent", has_docs=True) is True
    assert _allows_background_evaluation("chat_agent", has_docs=True) is False
    assert _allows_background_evaluation("evaluation_agent", has_docs=True) is False
    assert _allows_background_evaluation("research_agent", has_docs=False) is False


# ── AgentRoute evaluate_after ─────────────────────────────────────────────────

def test_agent_route_evaluate_after_defaults_false():
    r = AgentRoute(intent="retrieval", agent_name="retrieval_agent",
                   prompt_name="retrieval_capability")
    assert r.evaluate_after is False


def test_agent_route_evaluate_after_can_be_set():
    r = AgentRoute(intent="research", agent_name="research_agent",
                   prompt_name="research_writer", evaluate_after=True)
    assert r.evaluate_after is True


# ── ExecutionPlan ─────────────────────────────────────────────────────────────

def test_execution_plan_can_include_final_composition():
    route = AgentRoute(
        intent="research",
        agent_name="research_agent",
        prompt_name="research_writer",
        compose_after=True,
    )
    plan = _build_execution_plan(route, trace_id="trace-1", observation_id="research-obs")

    assert len(plan.steps) == 2
    assert plan.trace_id == "trace-1"
    assert plan.primary_step.trace_id == "trace-1"
    assert plan.primary_step.observation_id == "research-obs"
    assert plan.composition_step is not None
    assert plan.composition_step.agent_name == "chat_agent"
    assert plan.composition_step.trace_id == "trace-1"
    assert plan.composition_step.observation_id != "research-obs"


def test_execution_plan_does_not_compose_chat_routes():
    route = AgentRoute(
        intent="chat",
        agent_name="chat_agent",
        prompt_name="chat_mode",
        compose_after=True,
    )
    plan = _build_execution_plan(route, trace_id="trace-1", observation_id="chat-obs")

    assert len(plan.steps) == 1
    assert plan.composition_step is None


def test_execution_plan_collects_evidence_for_document_questions():
    route = AgentRoute(
        intent="question",
        agent_name="question_agent",
        prompt_name="question_skill",
    )
    plan = _build_execution_plan(
        route,
        trace_id="trace-1",
        observation_id="question-obs",
        collect_evidence=True,
    )

    assert len(plan.steps) == 2
    assert plan.evidence_step is not None
    assert plan.evidence_step.intent == "retrieval"
    assert plan.evidence_step.trace_id == "trace-1"
    assert plan.target_step.intent == "question"
    assert plan.target_step.observation_id == "question-obs"


def test_routes_always_have_evaluate_after_false_by_default(monkeypatch):
    for agent_name in ["chat_agent", "question_agent", "retrieval_agent"]:
        monkeypatch.setattr("agents.router_agent._orchestrate", _fake_orchestrate(agent_name))
        route = asyncio.run(classify_intent("message", [1]))
        assert route.evaluate_after is False, f"expected False for agent: {agent_name}"


# ── question_agent uses question_default stack ────────────────────────────────

def test_question_agent_uses_question_default_stack():
    from agents.question_agent import STACK_NAME
    assert STACK_NAME == "question_default"


def test_question_default_stack_loads():
    from prompting.loader import load_stack
    stack = load_stack("question_default")
    assert any(p.base_name == "question_skill" for p in stack.prompts)
    assert not any(p.base_name == "retrieval_capability" for p in stack.prompts)
    assert not any(p.base_name == "chat_mode" for p in stack.prompts)


def test_chat_default_stack_has_no_retrieval_prompt():
    from prompting.loader import load_stack
    stack = load_stack("chat_default")
    assert any(p.base_name == "chat_mode" for p in stack.prompts)
    assert not any(p.base_name == "retrieval_capability" for p in stack.prompts)


def test_chat_and_question_agents_use_no_tool_runner():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    chat_source = (root / "agents" / "chat_agent.py").read_text(encoding="utf-8")
    question_source = (root / "agents" / "question_agent.py").read_text(encoding="utf-8")

    assert "run_tool_agent" not in chat_source
    assert "run_tool_agent" not in question_source
    assert "run_research_task" not in question_source
    assert "run_no_tool_agent" in chat_source
    assert "run_no_tool_agent" in question_source


def test_retrieval_agent_uses_tool_runner():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    retrieval_source = (root / "agents" / "retrieval_agent.py").read_text(encoding="utf-8")

    assert "run_tool_agent" in retrieval_source
    assert "run_tool_agent_stream" in retrieval_source


def test_removed_legacy_agent_aliases_are_gone():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    runner_source = (root / "agents" / "runner.py").read_text(encoding="utf-8")

    assert not (root / "agents" / "main_agent.py").exists()
    assert not (root / "agents" / "orchestrator_agent.py").exists()
    assert "run_specialist_agent" not in runner_source
    assert "run_specialist_agent_stream" not in runner_source


def test_removed_agent_tools_module_is_gone():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]

    assert not (root / "agents" / "agent_tools.py").exists()


# ── chat_mode no longer has sub-agent tool instructions ──────────────────────

def test_chat_mode_has_no_sub_agent_tool_instructions():
    from prompting.registry import get
    content = get("chat_mode")
    for tool in ("call_question_agent", "call_retrieval_agent",
                 "call_research_agent", "call_evaluation_agent"):
        assert tool not in content, f"{tool!r} should not be in chat_mode.txt"


# ── route_coordinator prompt ──────────────────────────────────────────────────

def test_route_coordinator_prompt_exists():
    from prompting.registry import get, exists
    assert exists("route_coordinator")
    content = get("route_coordinator")
    assert len(content) > 100
    assert "agent_name" in content


def test_router_default_stack_loads():
    from prompting.loader import load_stack
    stack = load_stack("router_default")
    assert any(p.base_name == "route_coordinator" for p in stack.prompts)


# ── agent execution root filter ───────────────────────────────────────────────

def test_agent_execution_root_filter_is_importable():
    from services.trace_read.common import _agent_execution_root
    condition = _agent_execution_root()
    assert condition is not None


# ── route_agent_message integration tests ────────────────────────────────────

def test_router_message_can_compose_after_task_result(monkeypatch):
    from agents.router_agent import route_agent_message
    from agents.types import AgentResult

    calls = []

    async def fake_research(*args, **kwargs):
        calls.append(("research", kwargs))
        return AgentResult(
            response="research answer",
            sources=["paper.pdf p.4"],
            task_type="research_task",
            route_intent="research",
            agent_name="research_agent",
            prompt_name="research_writer",
            observation_id=kwargs.get("observation_id"),
        )

    async def fake_compose(**kwargs):
        calls.append(("compose", kwargs))
        return AgentResult(
            response="composed answer",
            sources=kwargs["task_result"].sources,
            task_type="response_composition",
            route_intent="chat",
            agent_name="chat_agent",
            prompt_name="chat_mode",
            observation_id=kwargs.get("observation_id"),
        )

    monkeypatch.setattr("agents.router_agent._write_router_trace", lambda *_, **__: None)
    monkeypatch.setattr("agents.router_agent.run_research_agent", fake_research)
    monkeypatch.setattr("agents.chat_agent.compose_final_response", fake_compose)
    monkeypatch.setattr("agents.router_agent.propagate_attributes", lambda **_: contextlib.nullcontext())

    route = AgentRoute(
        intent="research",
        agent_name="research_agent",
        prompt_name="research_writer",
        compose_after=True,
    )
    result = asyncio.run(route_agent_message(
        "research this",
        "thread-1",
        [1],
        route=route,
        trace_id="trace-1",
    ))

    assert result.response == "composed answer"
    assert [name for name, _ in calls] == ["research", "compose"]
    assert calls[0][1]["trace_id"] == "trace-1"
    assert calls[0][1]["observation_id"]
    assert calls[1][1]["trace_id"] == "trace-1"
    assert calls[1][1]["observation_id"]
    assert calls[1][1]["task_result"].response == "research answer"


def test_router_document_question_collects_evidence_then_questions(monkeypatch):
    from agents.router_agent import route_agent_message
    from agents.types import AgentResult

    calls = []

    async def fake_retrieval(*args, **kwargs):
        calls.append(("retrieval", kwargs))
        return AgentResult(
            response="evidence bundle",
            sources=["paper.pdf p.2"],
            task_type="retrieval_qa",
            route_intent="question",
            agent_name="retrieval_agent",
            prompt_name="retrieval_capability",
            observation_id=kwargs.get("observation_id"),
        )

    async def fake_question(*args, **kwargs):
        calls.append(("question", kwargs))
        return AgentResult(
            response="Q1? Q2?",
            sources=kwargs.get("evidence_sources") or [],
            task_type="question_generation",
            route_intent="question",
            agent_name="question_agent",
            prompt_name="question_skill",
            observation_id=kwargs.get("observation_id"),
        )

    monkeypatch.setattr("agents.router_agent._write_router_trace", lambda *_, **__: None)
    monkeypatch.setattr("agents.retrieval_agent.answer", fake_retrieval)
    monkeypatch.setattr("agents.question_agent.answer", fake_question)
    monkeypatch.setattr("agents.router_agent.propagate_attributes", lambda **_: contextlib.nullcontext())

    route = AgentRoute(
        intent="question",
        agent_name="question_agent",
        prompt_name="question_skill",
    )
    result = asyncio.run(route_agent_message(
        "make questions from this paper",
        "thread-1",
        [1],
        route=route,
        trace_id="trace-1",
    ))

    assert result.response == "Q1? Q2?"
    assert [name for name, _ in calls] == ["retrieval", "question"]
    assert calls[0][1]["route_intent"] == "question"
    assert calls[1][1]["evidence_context"] == "evidence bundle"
    assert calls[1][1]["evidence_sources"] == ["paper.pdf p.2"]
    assert calls[0][1]["trace_id"] == "trace-1"
    assert calls[0][1]["observation_id"]
    assert calls[1][1]["trace_id"] == "trace-1"
    assert calls[1][1]["observation_id"]


def test_research_retriever_uses_task_context_fields(monkeypatch):
    from agents.research.retriever import retrieve_evidence

    seen_contexts = []

    def fake_search_report(**kwargs):
        ctx = kwargs["ctx"]
        seen_contexts.append(ctx)
        return '{"results":[]}'

    monkeypatch.setattr("agents.research.retriever.run_search_report", fake_search_report)

    chunks, sources = asyncio.run(retrieve_evidence(
        query="method evidence",
        document_ids=[1],
        seen_chunks=set(),
        task_type="research_task",
        route_intent="research",
    ))

    assert chunks == []
    assert sources == []
    assert seen_contexts[0].task_type == "research_task"
    assert seen_contexts[0].route_intent == "research"
    assert not hasattr(seen_contexts[0], "mode")
