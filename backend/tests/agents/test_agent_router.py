import asyncio
import contextlib

import pytest

from agents.router_agent import (
    RouterDecision,
    _allows_background_evaluation,
    _build_execution_plan,
    _keyword_classify,
    _normalise_decision,
    classify_intent,
)
from agents.types import AgentRoute


# ── Keyword fast-path ─────────────────────────────────────────────────────────

def test_router_detects_research_with_documents():
    route = asyncio.run(classify_intent("請整理研究動機、研究方法、研究成果與限制", [1]))
    assert route.intent == "research"
    assert route.agent_name == "research_agent"
    assert route.prompt_name == "research_writer"


def test_router_detects_question_generation_with_documents():
    route = asyncio.run(classify_intent("請出三題導讀問題給我", [1]))
    assert route.intent == "question"
    assert route.agent_name == "question_agent"
    assert route.prompt_name == "question_skill"


def test_router_detects_retrieval_when_documents_are_attached():
    route = asyncio.run(classify_intent("這篇論文的主題是什麼？", [1]))
    assert route.intent == "retrieval"
    assert route.agent_name == "retrieval_agent"


def test_router_defaults_to_chat_without_documents():
    route = asyncio.run(classify_intent("我想討論一個研究方向"))
    assert route.intent == "chat"
    assert route.agent_name == "chat_agent"


def test_router_keyword_fast_path_skips_llm(monkeypatch):
    async def fail_if_called(*_args, **_kwargs):
        raise AssertionError("LLM fallback should not run for keyword routes")

    monkeypatch.setattr("agents.router_agent._llm_classify_intent", fail_if_called)
    route = asyncio.run(classify_intent("幫我摘要這份文件", [1]))
    assert route.intent == "research"


# ── Evaluation intent (keyword path) ─────────────────────────────────────────

def test_router_detects_evaluation_keyword_no_docs():
    intent = _keyword_classify("評估一下剛才的回答好不好", has_docs=False)
    assert intent == "evaluation"


def test_router_detects_evaluation_keyword_with_docs():
    intent = _keyword_classify("check quality", has_docs=True)
    assert intent == "evaluation"


def test_router_evaluation_route_has_correct_agent():
    route = asyncio.run(classify_intent("評估一下剛才的回答好不好"))
    assert route.intent == "evaluation"
    assert route.agent_name == "evaluation_agent"
    assert route.evaluate_after is False


# ── LLM fallback returns RouterDecision ──────────────────────────────────────

def test_router_uncertain_uses_llm_once(monkeypatch):
    calls = 0

    async def fake_llm(message: str, has_docs: bool) -> RouterDecision:
        nonlocal calls
        calls += 1
        assert message == "幫我處理一下"
        assert has_docs is True
        return RouterDecision(intent="research", evaluate_after=False, reason="test")

    monkeypatch.setattr("agents.router_agent._llm_classify_intent", fake_llm)
    route = asyncio.run(classify_intent("幫我處理一下", [1]))

    assert calls == 1
    assert route.intent == "research"
    assert route.evaluate_after is False


def test_router_llm_evaluate_after_propagates_to_route(monkeypatch):
    async def fake_llm(message: str, has_docs: bool) -> RouterDecision:
        return RouterDecision(intent="research", evaluate_after=True, reason="user asked")

    monkeypatch.setattr("agents.router_agent._llm_classify_intent", fake_llm)
    route = asyncio.run(classify_intent("幫我分析完之後確認有沒有漏掉重點", [1]))

    assert route.intent == "research"
    assert route.evaluate_after is True


# ── RouterDecision model ──────────────────────────────────────────────────────

def test_router_decision_default_fields():
    d = RouterDecision(intent="chat")
    assert d.evaluate_after is False
    assert d.reason == ""


def test_router_decision_all_fields():
    d = RouterDecision(intent="research", evaluate_after=True, reason="user said so")
    assert d.intent == "research"
    assert d.evaluate_after is True
    assert d.reason == "user said so"


def test_normalise_decision_clears_evaluate_after_for_chat():
    d = RouterDecision(intent="chat", evaluate_after=True)
    result = _normalise_decision(d, has_docs=True)
    assert result.evaluate_after is False


def test_normalise_decision_clears_evaluate_after_for_evaluation():
    d = RouterDecision(intent="evaluation", evaluate_after=True)
    result = _normalise_decision(d, has_docs=False)
    assert result.evaluate_after is False


def test_normalise_decision_preserves_evaluate_after_for_research():
    d = RouterDecision(intent="research", evaluate_after=True, reason="ok")
    result = _normalise_decision(d, has_docs=True)
    assert result.evaluate_after is True


def test_normalise_decision_preserves_evaluate_after_for_document_question():
    d = RouterDecision(intent="question", evaluate_after=True, reason="ok")
    result = _normalise_decision(d, has_docs=True)
    assert result.evaluate_after is True


def test_normalise_decision_preserves_evaluate_after_for_document_retrieval():
    d = RouterDecision(intent="retrieval", evaluate_after=True, reason="ok")
    result = _normalise_decision(d, has_docs=True)
    assert result.evaluate_after is True


def test_normalise_decision_gates_research_on_docs():
    d = RouterDecision(intent="research", evaluate_after=True)
    result = _normalise_decision(d, has_docs=False)
    assert result.intent == "chat"
    assert result.evaluate_after is False


def test_background_evaluation_policy_is_document_grounded_only():
    assert _allows_background_evaluation("research", has_docs=True) is True
    assert _allows_background_evaluation("retrieval", has_docs=True) is True
    assert _allows_background_evaluation("question", has_docs=True) is True
    assert _allows_background_evaluation("chat", has_docs=True) is False
    assert _allows_background_evaluation("evaluation", has_docs=True) is False
    assert _allows_background_evaluation("research", has_docs=False) is False


# ── AgentRoute has evaluate_after field ──────────────────────────────────────

def test_agent_route_evaluate_after_defaults_false():
    r = AgentRoute(intent="retrieval", agent_name="retrieval_agent",
                   prompt_name="retrieval_capability")
    assert r.evaluate_after is False


def test_agent_route_evaluate_after_can_be_set():
    r = AgentRoute(intent="research", agent_name="research_agent",
                   prompt_name="research_writer", evaluate_after=True)
    assert r.evaluate_after is True


def test_execution_plan_can_include_final_composition():
    route = AgentRoute(
        intent="research",
        agent_name="research_agent",
        prompt_name="research_writer",
        compose_after=True,
    )
    plan = _build_execution_plan(route, router_run_id="router-run", task_run_id="research-run")

    assert len(plan.steps) == 2
    assert plan.primary_step.run_id == "research-run"
    assert plan.primary_step.parent_run_id == "router-run"
    assert plan.composition_step is not None
    assert plan.composition_step.agent_name == "chat_agent"
    assert plan.composition_step.parent_run_id == "router-run"


def test_execution_plan_does_not_compose_chat_routes():
    route = AgentRoute(
        intent="chat",
        agent_name="chat_agent",
        prompt_name="chat_mode",
        compose_after=True,
    )
    plan = _build_execution_plan(route, router_run_id="router-run", task_run_id="chat-run")

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
        router_run_id="router-run",
        task_run_id="question-run",
        collect_evidence=True,
    )

    assert len(plan.steps) == 2
    assert plan.evidence_step is not None
    assert plan.evidence_step.intent == "retrieval"
    assert plan.target_step.intent == "question"
    assert plan.target_step.run_id == "question-run"


def test_keyword_routes_always_have_evaluate_after_false():
    for message, doc_ids in [
        ("幫我摘要", [1]),
        ("出題", [1]),
        ("這篇的主題", [1]),
        ("你好", []),
    ]:
        route = asyncio.run(classify_intent(message, doc_ids))
        assert route.evaluate_after is False, f"expected False for: {message!r}"


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


# ── route_coordinator prompt exists and loads ─────────────────────────────────

def test_route_coordinator_prompt_exists():
    from prompting.registry import get, exists
    assert exists("route_coordinator")
    content = get("route_coordinator")
    assert len(content) > 100
    assert "intent" in content


def test_router_default_stack_loads():
    from prompting.loader import load_stack
    stack = load_stack("router_default")
    assert any(p.base_name == "route_coordinator" for p in stack.prompts)


# ── agent execution root filter covers both old and new trace formats ─────────

def test_agent_execution_root_filter_is_importable():
    from api.traces import _agent_execution_root
    condition = _agent_execution_root()
    assert condition is not None


def test_chat_composition_is_no_tool_child_step(monkeypatch):
    from agents.chat_agent import COMPOSITION_TASK_TYPE, compose_final_response
    from agents.types import AgentResult

    writes = []

    def fake_write(*args, **kwargs):
        writes.append((args, kwargs))

    async def fake_generation(runnable, input_value, *, prompt_name: str, name: str = "AzureChatOpenAI", metadata=None):
        assert prompt_name == "chat_mode"
        assert "final-composition" in name
        assert metadata["prompt_stack_name"] == "chat_default"
        assert input_value[-1].type == "human"
        return type("Response", (), {"content": "formatted answer"})()

    monkeypatch.setattr("agents.chat_agent._write_composition_trace", fake_write)
    monkeypatch.setattr("agents.chat_agent._llm", lambda use_mini=False: object())
    monkeypatch.setattr("agents.chat_agent.ainvoke_traced_generation", fake_generation)
    monkeypatch.setattr("agents.chat_agent.propagate_attributes", lambda **_: contextlib.nullcontext())

    result = asyncio.run(compose_final_response(
        user_message="Please format this",
        task_result=AgentResult(
            response="task answer",
            sources=["paper.pdf p.3"],
            task_type="research_task",
            route_intent="research",
            agent_name="research_agent",
            prompt_name="research_writer",
        ),
        thread_id="thread-1",
        document_ids=[1],
        run_id="composition-run",
        parent_run_id="router-run",
    ))

    assert result.response == "formatted answer"
    assert result.task_type == COMPOSITION_TASK_TYPE
    assert result.route_intent == "chat"
    assert result.trace_run_id == "composition-run"
    assert writes[0][1]["parent_run_id"] == "router-run"
    assert writes[-1][1]["output"]["answer"] == "formatted answer"


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
            trace_run_id=kwargs.get("run_id"),
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
            trace_run_id=kwargs.get("run_id"),
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
        run_id="router-run",
    ))

    assert result.response == "composed answer"
    assert [name for name, _ in calls] == ["research", "compose"]
    assert calls[0][1]["parent_run_id"] == "router-run"
    assert calls[1][1]["parent_run_id"] == "router-run"
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
            trace_run_id=kwargs.get("run_id"),
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
            trace_run_id=kwargs.get("run_id"),
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
        run_id="router-run",
    ))

    assert result.response == "Q1? Q2?"
    assert [name for name, _ in calls] == ["retrieval", "question"]
    assert calls[0][1]["route_intent"] == "question"
    assert calls[1][1]["evidence_context"] == "evidence bundle"
    assert calls[1][1]["evidence_sources"] == ["paper.pdf p.2"]
    assert calls[0][1]["parent_run_id"] == "router-run"
    assert calls[1][1]["parent_run_id"] == "router-run"


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
