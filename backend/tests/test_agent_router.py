import asyncio

from agents.main_agent import classify_intent


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
    assert route.agent_name == "orchestrator_agent"


def test_router_keyword_fast_path_skips_llm(monkeypatch):
    async def fail_if_called(*_args, **_kwargs):
        raise AssertionError("LLM fallback should not run for keyword routes")

    monkeypatch.setattr("agents.main_agent._llm_classify_intent", fail_if_called)

    route = asyncio.run(classify_intent("幫我摘要這份文件", [1]))

    assert route.intent == "research"


def test_router_uncertain_uses_llm_once(monkeypatch):
    calls = 0

    async def fake_llm(message: str, has_docs: bool):
        nonlocal calls
        calls += 1
        assert message == "幫我處理一下"
        assert has_docs is True
        return "research"

    monkeypatch.setattr("agents.main_agent._llm_classify_intent", fake_llm)

    route = asyncio.run(classify_intent("幫我處理一下", [1]))

    assert calls == 1
    assert route.intent == "research"
