from collections.abc import AsyncIterator
import json
import logging

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import AzureChatOpenAI
from pydantic import BaseModel, Field

from config import settings
from prompting.loader import load_stack
from prompting.registry import get as get_prompt
from prompting.registry import version as prompt_version

from .types import AgentRoute, AgentResult
from langfuse import observe, propagate_attributes
import contextlib

logger = logging.getLogger(__name__)

ROUTER_PROMPT_NAME = "intent_router"
VALID_INTENTS = {"chat", "retrieval", "research", "summary", "question"}
ROUTER_LLM_INTENTS = {"chat", "retrieval", "research", "summary", "question"}

# Maximum number of times a single request may be re-routed between agents.
# If an agent tries to hand off more than this many times, we fall back to chat.
MAX_HANDOFFS = 3

_FOLLOWUP_SIGNALS = (
    "繼續", "再說", "補充", "更多", "詳細", "展開", "那", "那麼", "這個",
    "剛才", "你說的", "上面", "除此之外", "另外", "而且", "還有",
    "more", "continue", "elaborate", "expand", "follow up",
)


def _looks_like_followup(message: str) -> bool:
    """Short messages that appear to continue a previous topic."""
    text = (message or "").strip()
    if len(text) > 60:
        return False
    lower = text.lower()
    return any(sig in lower for sig in _FOLLOWUP_SIGNALS)


_RESEARCH_KEYWORDS = (
    "研究動機",
    "研究方法",
    "研究成果",
    "研究限制",
    "方法與結果",
    "動機方法成果",
    "motivation",
    "method",
    "results",
    "limitations",
)

_SUMMARY_KEYWORDS = (
    "摘要",
    "總結",
    "重點整理",
    "懶人包",
    "summary",
    "summarize",
    "overview",
)

_QUESTION_KEYWORDS = (
    "出題",
    "問我",
    "測驗",
    "題目",
    "導讀",
    "帶我理解",
    "帶我學",
    "興趣量表",
    "練習",
    "question",
    "questions",
    "quiz",
    "survey",
)

_RETRIEVAL_KEYWORDS = (
    "這篇",
    "這份",
    "主題",
    "在講什麼",
    "哪一頁",
    "引用",
    "來源",
    "根據文件",
    "根據 pdf",
    "根據pdf",
    "這段",
    "pdf",
    "source",
    "cite",
)


class RouterDecision(BaseModel):
    intent: str = Field(description="One of: chat, retrieval, research, question, summary")


def _prompt_key(thread_id: str | None, document_ids: list[int] | None) -> str:
    doc_key = ",".join(str(doc_id) for doc_id in sorted(document_ids or []))
    return f"{thread_id or ''}:{doc_key}"


def _normalise_intent(intent: str) -> str:
    value = (intent or "chat").strip().lower()
    if value == "summary":
        return "research"
    if value not in VALID_INTENTS:
        return "chat"
    return value


def _primary_prompt(stack_name: str, base_name: str, thread_id: str | None, document_ids: list[int] | None):
    stack = load_stack(stack_name, _prompt_key(thread_id, document_ids))
    return next((p for p in stack.prompts if p.base_name == base_name), stack.prompts[-1])


def _route_for_intent(
    intent: str,
    document_ids: list[int] | None = None,
    thread_id: str | None = None,
) -> AgentRoute:
    original_intent = (intent or "chat").strip().lower()
    route_intent = _normalise_intent(intent)
    if route_intent == "research":
        prompt = _primary_prompt("research_runtime", "research_writer", thread_id, document_ids)
        return AgentRoute(
            "research",
            "research_agent",
            prompt.name,
            prompt.version,
            original_intent=original_intent,
            resolved_intent="research",
        )
    if route_intent == "question":
        return AgentRoute(
            "question",
            "question_agent",
            "question_skill",
            prompt_version("question_skill"),
            original_intent=original_intent,
            resolved_intent="question",
        )
    if route_intent == "retrieval":
        return AgentRoute(
            "retrieval",
            "retrieval_agent",
            "retrieval_capability",
            prompt_version("retrieval_capability"),
            original_intent=original_intent,
            resolved_intent="retrieval",
        )
    return AgentRoute(
        "chat",
        "orchestrator_agent",
        "chat_mode",
        prompt_version("chat_mode"),
        original_intent=original_intent,
        resolved_intent="chat",
    )


def _keyword_classify(message: str, has_docs: bool) -> str:
    text = (message or "").lower()
    if not has_docs:
        return "chat"
    if any(k.lower() in text for k in _QUESTION_KEYWORDS):
        return "question"
    if any(k.lower() in text for k in _SUMMARY_KEYWORDS):
        return "summary"
    if any(k.lower() in text for k in _RESEARCH_KEYWORDS):
        return "research"
    if any(k.lower() in text for k in _RETRIEVAL_KEYWORDS):
        return "retrieval"
    return "uncertain"


def _router_llm():
    return AzureChatOpenAI(
        azure_deployment=settings.azure_mini_deployment or settings.azure_chat_deployment,
        azure_endpoint=settings.azure_openai_endpoint,
        api_key=settings.azure_openai_api_key,
        api_version=settings.azure_openai_api_version,
        temperature=0,
    )


async def _llm_classify_intent(message: str, has_docs: bool) -> str:
    """Use intent_router for ambiguous requests. Falls back conservatively."""
    system = get_prompt(ROUTER_PROMPT_NAME)
    user = {
        "message": message,
        "has_document_ids": has_docs,
        "allowed_intents": ["chat", "retrieval", "research", "question"],
        "response_format": {"intent": "chat|retrieval|research|question"},
    }
    try:
        structured = _router_llm().with_structured_output(RouterDecision)
        decision: RouterDecision = await structured.ainvoke(
            [
                SystemMessage(content=system),
                HumanMessage(content=json.dumps(user, ensure_ascii=False)),
            ]
        )
        intent = (decision.intent or "chat").strip().lower()
        if intent not in VALID_INTENTS:
            intent = "chat"
    except Exception as exc:
        logger.warning("LLM router failed; using conservative fallback: %s", exc)
        return "retrieval" if has_docs else "chat"
    if _normalise_intent(intent) in {"research", "retrieval"} and not has_docs:
        return "chat"
    return intent


@observe(as_type="chain")
async def classify_intent(
    message: str,
    document_ids: list[int] | None = None,
    thread_id: str | None = None,
    previous_intent: str | None = None,
) -> AgentRoute:
    with propagate_attributes(session_id=thread_id) if thread_id else contextlib.nullcontext():
        """Route a request with a deterministic fast path and an LLM fallback.

        previous_intent: the intent of the last turn in this conversation.
        Follow-up messages in research/retrieval conversations stay in the same
        mode rather than falling through to chat.
        """
        has_docs = bool(document_ids)
        intent = _keyword_classify(message, has_docs)
        if intent == "uncertain":
            # Inherit previous intent for clear follow-up messages so "還有呢" or
            # "能補充嗎" don't get misrouted to chat mid-research.
            if previous_intent in ("research", "retrieval") and _looks_like_followup(message):
                intent = previous_intent
            else:
                intent = await _llm_classify_intent(message, has_docs)
        return _route_for_intent(intent, document_ids, thread_id)


@observe(as_type="agent")
async def _run_research_agent(
    user_message: str,
    thread_id: str,
    document_ids: list[int] | None,
    *,
    run_id: str | None,
    on_stage=None,
    original_intent: str | None = None,
    resolved_intent: str | None = None,
) -> AgentResult:
    with propagate_attributes(session_id=thread_id) if thread_id else contextlib.nullcontext():
        from .research_agent import run_research_task
    
        stack = load_stack("research_runtime", _prompt_key(thread_id, document_ids))
        metadata = {
            "mode": "research",
            "agent_name": "research_agent",
            "original_intent": original_intent or "research",
            "resolved_intent": resolved_intent or "research",
            **stack.metadata(),
        }
        return await run_research_task(
            question=user_message,
            thread_id=thread_id,
            document_ids=document_ids or [],
            mode="research",
            metadata=metadata,
            run_id=run_id,
            on_stage=on_stage,
            max_searches=10,
            max_consecutive_no_new=2,
        )


@observe(as_type="agent")
async def _run_chat_agent(
    user_message: str,
    thread_id: str,
    document_ids: list[int] | None,
    *,
    run_id: str | None,
    on_stage=None,
    use_mini: bool = False,
    original_intent: str | None = None,
    resolved_intent: str | None = None,
) -> AgentResult:
    with propagate_attributes(session_id=thread_id) if thread_id else contextlib.nullcontext():
        from .orchestrator_agent import answer as _chat_answer
        return await _chat_answer(
            user_message,
            thread_id,
            document_ids,
            run_id=run_id,
            on_stage=on_stage,
            use_mini=use_mini,
            original_intent=original_intent,
            resolved_intent=resolved_intent,
        )


@observe(as_type="chain")
async def route_agent_message(
    user_message: str,
    thread_id: str,
    document_ids: list[int] | None = None,
    *,
    route: AgentRoute | None = None,
    run_id: str | None = None,
    _hop_count: int = 0,
    use_mini: bool = False,
) -> AgentResult:
    with propagate_attributes(session_id=thread_id) if thread_id else contextlib.nullcontext():
        """Route a user message to the appropriate specialist agent."""
        from . import orchestrator_agent, question_agent, retrieval_agent
    
        if _hop_count >= MAX_HANDOFFS:
            logger.warning(
                "Handoff limit reached (%d/%d) thread=%s; falling back to chat",
                _hop_count,
                MAX_HANDOFFS,
                thread_id,
            )
            return await orchestrator_agent.answer(user_message, thread_id, document_ids,
                                                    run_id=run_id, use_mini=use_mini)
    
        route = route or await classify_intent(user_message, document_ids, thread_id)
        route_intent = _normalise_intent(route.intent)
        if route_intent == "research":
            return await _run_research_agent(
                user_message,
                thread_id,
                document_ids,
                run_id=run_id,
                original_intent=route.original_intent,
                resolved_intent=route.resolved_intent,
            )
        if route_intent == "question":
            return await question_agent.answer(
                user_message,
                thread_id,
                document_ids,
                run_id=run_id,
                use_mini=use_mini,
                original_intent=route.original_intent,
                resolved_intent=route.resolved_intent,
            )
        if route_intent == "retrieval":
            return await retrieval_agent.answer(
                user_message,
                thread_id,
                document_ids,
                mode=route_intent,
                run_id=run_id,
                use_mini=use_mini,
                original_intent=route.original_intent,
                resolved_intent=route.resolved_intent,
            )
        return await orchestrator_agent.answer(user_message, thread_id, document_ids,
                                                run_id=run_id, use_mini=use_mini,
                                                original_intent=route.original_intent,
                                                resolved_intent=route.resolved_intent)


@observe(as_type="chain")
async def route_agent_stream(
    user_message: str,
    thread_id: str,
    document_ids: list[int] | None = None,
    *,
    route: AgentRoute | None = None,
    run_id: str | None = None,
    _hop_count: int = 0,
    use_mini: bool = False,
) -> AsyncIterator[tuple[str, bool, list[str]]]:
    with propagate_attributes(session_id=thread_id) if thread_id else contextlib.nullcontext():
        """Stream tokens from the appropriate specialist agent."""
        from . import orchestrator_agent, question_agent, retrieval_agent
    
        if _hop_count >= MAX_HANDOFFS:
            logger.warning(
                "Stream handoff limit reached (%d/%d) thread=%s; falling back to chat",
                _hop_count,
                MAX_HANDOFFS,
                thread_id,
            )
            async for item in orchestrator_agent.stream(user_message, thread_id, document_ids,
                                                         run_id=run_id, use_mini=use_mini):
                yield item
            return
    
        route = route or await classify_intent(user_message, document_ids, thread_id)
        route_intent = _normalise_intent(route.intent)
        if route_intent == "research":
            result = await _run_research_agent(
                user_message,
                thread_id,
                document_ids,
                run_id=run_id,
                original_intent=route.original_intent,
                resolved_intent=route.resolved_intent,
            )
            yield result.response, False, []
            yield "", True, result.sources
            return
        if route_intent == "question":
            async for item in question_agent.stream(user_message, thread_id, document_ids,
                                                      run_id=run_id, use_mini=use_mini,
                                                      original_intent=route.original_intent,
                                                      resolved_intent=route.resolved_intent):
                yield item
            return
        if route_intent == "retrieval":
            async for item in retrieval_agent.stream(
                user_message,
                thread_id,
                document_ids,
                mode=route_intent,
                run_id=run_id,
                use_mini=use_mini,
                original_intent=route.original_intent,
                resolved_intent=route.resolved_intent,
            ):
                yield item
            return
        async for item in orchestrator_agent.stream(user_message, thread_id, document_ids,
                                                     run_id=run_id, use_mini=use_mini,
                                                     original_intent=route.original_intent,
                                                     resolved_intent=route.resolved_intent):
            yield item
