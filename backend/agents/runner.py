import contextlib
import json
import logging
import re
import warnings
from typing import Callable
from pydantic import BaseModel, Field
from langchain.agents import create_agent
from langchain.agents.middleware import before_model
from langchain.agents.structured_output import ProviderStrategy
from langchain_openai import AzureChatOpenAI
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langfuse.langchain import CallbackHandler
from langfuse import observe, propagate_attributes

warnings.filterwarnings(
    "ignore",
    message="Pydantic serializer warnings",
    category=UserWarning,
    module="pydantic",
)

from config import settings
from tools.rag_tool import (
    AgentContext,
    TOOLS,
    set_query_expander_llm,
)
from prompting.registry import get_langfuse_obj
from .agent_tools import AGENT_TOOLS

ORCHESTRATOR_TOOLS = TOOLS + AGENT_TOOLS

logger = logging.getLogger(__name__)


def _get_abstracts(document_ids: list[int] | None) -> list[dict]:
    """Fetch abstract_text for the given documents from PostgreSQL."""
    from database import SessionLocal, Document as DocModel
    db = SessionLocal()
    try:
        q = db.query(DocModel.id, DocModel.filename, DocModel.abstract_text).filter(
            DocModel.status == "ready"
        )
        if document_ids:
            q = q.filter(DocModel.id.in_(document_ids))
        return [
            {"filename": row.filename, "abstract": row.abstract_text}
            for row in q.all()
            if row.abstract_text
        ]
    finally:
        db.close()


def _attach_langfuse_prompt(metadata: dict | None) -> dict:
    """Attach Langfuse prompt object so CallbackHandler can link prompt->generation."""
    merged = dict(metadata or {})
    if merged.get("langfuse_prompt") is not None:
        return merged
    prompt_name = merged.get("prompt_name")
    if isinstance(prompt_name, str) and prompt_name:
        prompt_obj = get_langfuse_obj(prompt_name)
        if prompt_obj is not None:
            merged["langfuse_prompt"] = prompt_obj
    return merged



_checkpointer: AsyncPostgresSaver | None = None
_specialist_agent = None
_specialist_agent_mini = None
_orchestrator_agent = None
_orchestrator_agent_mini = None

# Runtime prompt behavior now comes from prompting.loader stacks injected per
# request.  Keep the agent-level system prompt intentionally small so prompt
# hot reload works without rebuilding the LangGraph agent.
SYSTEM_PROMPT = "You are an AI research assistant. Follow the request-specific system messages."



class AgentResponse(BaseModel):
    answer: str = Field(description="Complete answer to the user's question")
    sources: list[str] = Field(
        default_factory=list,
        description='Most relevant sources referenced (max 3), each as "filename p.N" (e.g. "report.pdf p.3")',
    )



_llm = AzureChatOpenAI(
    azure_deployment=settings.azure_chat_deployment,
    azure_endpoint=settings.azure_openai_endpoint,
    api_key=settings.azure_openai_api_key,
    api_version=settings.azure_openai_api_version,
    temperature=0.3,
)
_agent_llm = _llm.bind(parallel_tool_calls=False)
set_query_expander_llm(_llm)

_mini_llm = AzureChatOpenAI(
    azure_deployment=settings.azure_mini_deployment,
    azure_endpoint=settings.azure_openai_endpoint,
    api_key=settings.azure_openai_api_key,
    api_version=settings.azure_openai_api_version,
    temperature=0.3,
)
_mini_agent_llm = _mini_llm.bind(parallel_tool_calls=False)


@before_model
def _trim_messages(state: dict, runtime) -> dict | None:
    """Keep last 20 messages, always starting at a HumanMessage."""
    messages = state.get("messages", [])
    if len(messages) <= 20:
        return None
    tail = messages[-20:]
    for i, msg in enumerate(tail):
        if isinstance(msg, HumanMessage):
            tail = tail[i:]
            break
    return {"messages": tail}



async def setup_checkpointer():
    """Initialize AsyncPostgresSaver and build the agent. Called once at startup."""
    global _checkpointer, _specialist_agent, _specialist_agent_mini
    from psycopg_pool import AsyncConnectionPool
    pool = AsyncConnectionPool(
        conninfo=settings.database_url,
        kwargs={"autocommit": True},
        open=False,
    )
    await pool.open()
    _checkpointer = AsyncPostgresSaver(
        pool,
        serde=JsonPlusSerializer(allowed_msgpack_modules=[
            ("agent", "AgentResponse"),          # backward compat for pre-rename checkpoints
            ("agents.runner", "AgentResponse"),  # current module path
        ]),
    )
    await _checkpointer.setup()

    def _make_agent(llm, tools):
        return create_agent(
            llm,
            tools=tools,
            system_prompt=SYSTEM_PROMPT,
            middleware=[_trim_messages],
            response_format=ProviderStrategy(AgentResponse, strict=True),
            checkpointer=_checkpointer,
            context_schema=AgentContext,
        )

    _specialist_agent = _make_agent(_agent_llm, TOOLS)
    _specialist_agent_mini = _make_agent(_mini_agent_llm, TOOLS)

    global _orchestrator_agent, _orchestrator_agent_mini
    _orchestrator_agent = _make_agent(_agent_llm, ORCHESTRATOR_TOOLS)
    _orchestrator_agent_mini = _make_agent(_mini_agent_llm, ORCHESTRATOR_TOOLS)


def _get_specialist_agent(mini: bool = False):
    return _specialist_agent_mini if mini else _specialist_agent


def _get_orchestrator_agent(mini: bool = False):
    return _orchestrator_agent_mini if mini else _orchestrator_agent


def generate_title(messages: list[dict]) -> str:
    context = "\n".join(
        f"{m['role']}: {m['content'][:300]}" for m in messages[:4]
    )
    response = _llm.invoke([
        SystemMessage(content=(
            "根據以下對話內容，用繁體中文生成一個簡潔的對話標題（5-10字）。"
            "只回傳標題本身，不要加引號或其他說明。"
        )),
        HumanMessage(content=context),
    ])
    return response.content.strip()[:60]


async def get_thread_messages(thread_id: str) -> list[dict]:
    """Read conversation messages from the LangGraph checkpointer state."""
    if _checkpointer is None:
        return []
    config = {"configurable": {"thread_id": thread_id}}
    checkpoint_tuple = await _checkpointer.aget_tuple(config)
    if not checkpoint_tuple:
        return []
    messages = checkpoint_tuple.checkpoint.get("channel_values", {}).get("messages", [])
    result = []
    for msg in messages:
        if isinstance(msg, HumanMessage):
            content = msg.content if isinstance(msg.content, str) else str(msg.content)
            result.append({"role": "user", "content": content})
        elif isinstance(msg, AIMessage) and msg.content:
            content = msg.content if isinstance(msg.content, str) else str(msg.content)
            result.append({"role": "assistant", "content": _extract_answer(content)})
    return result


async def _get_structured_response(thread_id: str) -> AgentResponse | None:
    """Read the latest structured_response from the checkpointer."""
    if _checkpointer is None:
        return None
    checkpoint_tuple = await _checkpointer.aget_tuple(
        {"configurable": {"thread_id": thread_id}}
    )
    if not checkpoint_tuple:
        return None
    return checkpoint_tuple.checkpoint.get("channel_values", {}).get("structured_response")


def _get_last_research_context(thread_id: str) -> str | None:
    """Return a short excerpt from the most recent research/summary trace for this thread."""
    from database import SessionLocal, Trace
    import json as _json
    db = SessionLocal()
    try:
        row = (
            db.query(Trace.display)
            .filter(
                Trace.parent_run_id.is_(None),
                Trace.thread_id == thread_id,
                Trace.mode.in_(["research", "summary"]),
                Trace.error.is_(None),
            )
            .order_by(Trace.start_time.desc())
            .first()
        )
        if not row or not row[0]:
            return None
        display = _json.loads(row[0])
        answer = str(display.get("answer") or "").strip()
        if len(answer) < 50:
            return None
        # Keep at most 800 chars so we don't bloat the context window
        return answer[:800] + ("…" if len(answer) > 800 else "")
    except Exception:
        return None
    finally:
        db.close()


def _build_messages(
    user_message: str,
    document_ids: list[int] | None,
    task_prompt: str | list[str] | None = None,
    include_document_abstracts: bool = True,
    thread_id: str | None = None,
) -> list:
    """Build the message list for one agent turn.

    Injection order (all SystemMessages before the HumanMessage):
      1. task_prompt  — per-request instructions (e.g. extraction rules)
      2. document abstracts — background context for the referenced docs
      3. HumanMessage — the actual user question / trigger
    """
    messages = []
    if task_prompt:
        prompts = task_prompt if isinstance(task_prompt, list) else [task_prompt]
        for prompt in prompts:
            if prompt:
                messages.append(SystemMessage(content=prompt))
    if document_ids and include_document_abstracts:
        abstracts = _get_abstracts(document_ids)
        if abstracts:
            block = "\n\n".join(
                f"【{a['filename']}】\n{a['abstract']}" for a in abstracts
            )
            messages.append(SystemMessage(content=(
                "以下是本次對話引用的文件摘要，請以此作為背景資訊回答問題：\n\n"
                f"{block}"
            )))
    # If there is a prior research/summary result for this conversation, inject it
    # so follow-up questions can reference the structured evidence without re-searching.
    if thread_id:
        research_ctx = _get_last_research_context(thread_id)
        if research_ctx:
            messages.append(SystemMessage(content=(
                "【前次研究摘要】以下是這段對話中最近一次研究或摘要分析的結果，"
                "可作為追問的參考背景，但仍以文件原文為準：\n\n"
                f"{research_ctx}"
            )))
    messages.append(HumanMessage(content=user_message))
    return messages


@observe(as_type="agent")
async def run_specialist_agent(
    user_message: str,
    thread_id: str,
    document_ids: list[int] | None = None,
    metadata: dict | None = None,
    tracer_metadata: dict | None = None,
    run_id: str | None = None,
    on_stage: Callable[[str], None] | None = None,
    task_prompt: str | list[str] | None = None,
    recursion_limit: int = 30,
    include_document_abstracts: bool = True,
    max_searches: int | None = None,
    max_consecutive_empty: int | None = None,
    parent_run_id: str | None = None,
    use_mini: bool = False,
) -> tuple[str, list[str]]:
    import uuid as _uuid
    from tracer import LocalTracer
    agent = _get_specialist_agent(mini=use_mini)
    metadata = {**(metadata or {}), **(tracer_metadata or {})}
    metadata = _attach_langfuse_prompt(metadata)
    with propagate_attributes(version=metadata.get("prompt_version")) if metadata.get("prompt_version") else contextlib.nullcontext():
        tracer = LocalTracer(
        thread_id=thread_id,
        document_ids=document_ids,
        mode=metadata.get("mode"),
        agent_name=metadata.get("agent_name"),
        prompt_name=metadata.get("prompt_name"),
        prompt_version=metadata.get("prompt_version"),
        base_prompt_name=metadata.get("base_prompt_name"),
        task_prompt_name=metadata.get("task_prompt_name"),
        quality_prompt_name=metadata.get("quality_prompt_name"),
        base_prompt_hash=metadata.get("base_prompt_hash"),
        task_prompt_hash=metadata.get("task_prompt_hash"),
        quality_prompt_hash=metadata.get("quality_prompt_hash"),
        prompt_stack_name=metadata.get("prompt_stack_name"),
        prompt_stack_json=metadata.get("prompt_stack_json"),
        prompt_stack_tokens=metadata.get("prompt_stack_tokens"),
        quality_score=metadata.get("quality_score"),
        user_feedback=metadata.get("user_feedback"),
        parent_run_id=parent_run_id,
        original_intent=metadata.get("original_intent"),
        resolved_intent=metadata.get("resolved_intent"),
    )
    langfuse_handler = CallbackHandler()
    config: dict = {"configurable": {"thread_id": thread_id}, "callbacks": [tracer, langfuse_handler], "recursion_limit": recursion_limit}
    config["metadata"] = metadata
    if run_id:
        config["run_id"] = _uuid.UUID(run_id)

    async def _invoke():
        with propagate_attributes(session_id=thread_id):
            return await agent.ainvoke(
                {"messages": _build_messages(user_message, document_ids, task_prompt, include_document_abstracts, thread_id=thread_id)},
                config,
                context=AgentContext(
                    document_ids=document_ids,
                    on_stage=on_stage,
                    mode=metadata.get("mode"),
                    max_searches=max_searches,
                    max_consecutive_empty=max_consecutive_empty,
                    thread_id=thread_id,
                    run_id=run_id,
                ),
            )

    try:
        result = await _invoke()
    except Exception as e:
        if "StructuredOutputValidationError" in str(e) or "Extra data" in str(e):
            logger.warning("StructuredOutputValidationError on first attempt, retrying: %s", e)
            result = await _invoke()
        else:
            raise

    structured: AgentResponse | None = result.get("structured_response")
    if structured:
        return structured.answer, structured.sources
    ai_messages = [m for m in result["messages"] if isinstance(m, AIMessage) and m.content]
    raw = ai_messages[-1].content if ai_messages else "Unable to generate a response."
    return _extract_answer(raw), []


def _extract_answer(content: str) -> str:
    """Parse JSON from ProviderStrategy output and return the answer field.

    In a multi-step ReAct loop, multiple JSON objects may be concatenated
    (one per model call).  Scan all of them and return the answer from the
    last valid one.
    """
    # Fast path: single well-formed JSON
    try:
        parsed = json.loads(content)
        if isinstance(parsed, dict):
            answer = parsed.get("answer", "")
            if answer:
                return _strip_abstracts_block(str(answer))
    except Exception:
        pass

    # Slow path: scan through concatenated JSON objects
    last_answer: str | None = None
    decoder = json.JSONDecoder()
    idx = 0
    while idx < len(content):
        try:
            obj, end = decoder.raw_decode(content, idx)
            if isinstance(obj, dict) and obj.get("answer"):
                last_answer = str(obj["answer"])
            idx = end
        except Exception:
            idx += 1
    if last_answer is not None:
        return _strip_abstracts_block(last_answer)

    # Fallback: strip any leaked context and return raw
    return _strip_abstracts_block(content)


def _strip_abstracts_block(text: str) -> str:
    """Remove any <document_abstracts> block that leaked into a response."""
    if "<document_abstracts>" not in text:
        return text
    return re.sub(
        r"<document_abstracts>.*?</document_abstracts>\s*\n*",
        "",
        text,
        flags=re.DOTALL,
    ).lstrip()


@observe(as_type="agent")
async def run_specialist_agent_stream(
    user_message: str,
    thread_id: str,
    document_ids: list[int] | None = None,
    metadata: dict | None = None,
    tracer_metadata: dict | None = None,
    run_id: str | None = None,
    task_prompt: str | list[str] | None = None,
    include_document_abstracts: bool = True,
    max_searches: int | None = None,
    max_consecutive_empty: int | None = None,
    parent_run_id: str | None = None,
    use_mini: bool = False,
):
    """Async generator yielding (token, is_done, sources) tuples."""
    import uuid as _uuid
    from tracer import LocalTracer
    agent = _get_specialist_agent(mini=use_mini)
    metadata = {**(metadata or {}), **(tracer_metadata or {})}
    metadata = _attach_langfuse_prompt(metadata)
    with propagate_attributes(version=metadata.get("prompt_version")) if metadata.get("prompt_version") else contextlib.nullcontext():
        tracer = LocalTracer(
        thread_id=thread_id,
        document_ids=document_ids,
        mode=metadata.get("mode"),
        agent_name=metadata.get("agent_name"),
        prompt_name=metadata.get("prompt_name"),
        prompt_version=metadata.get("prompt_version"),
        base_prompt_name=metadata.get("base_prompt_name"),
        task_prompt_name=metadata.get("task_prompt_name"),
        quality_prompt_name=metadata.get("quality_prompt_name"),
        base_prompt_hash=metadata.get("base_prompt_hash"),
        task_prompt_hash=metadata.get("task_prompt_hash"),
        quality_prompt_hash=metadata.get("quality_prompt_hash"),
        prompt_stack_name=metadata.get("prompt_stack_name"),
        prompt_stack_json=metadata.get("prompt_stack_json"),
        prompt_stack_tokens=metadata.get("prompt_stack_tokens"),
        quality_score=metadata.get("quality_score"),
        user_feedback=metadata.get("user_feedback"),
        parent_run_id=parent_run_id,
        original_intent=metadata.get("original_intent"),
        resolved_intent=metadata.get("resolved_intent"),
    )
    langfuse_handler = CallbackHandler()
    config = {"configurable": {"thread_id": thread_id}, "callbacks": [tracer, langfuse_handler], "recursion_limit": 30, "metadata": metadata}
    if run_id:
        config["run_id"] = _uuid.UUID(run_id)

    async def _run_stream() -> str:
        with propagate_attributes(session_id=thread_id):
            content = ""
            async for chunk in agent.astream(
                {"messages": _build_messages(user_message, document_ids, task_prompt, include_document_abstracts, thread_id=thread_id)},
                config,
                context=AgentContext(
                    document_ids=document_ids,
                    mode=metadata.get("mode"),
                    max_searches=max_searches,
                    max_consecutive_empty=max_consecutive_empty,
                    thread_id=thread_id,
                    run_id=run_id,
                ),
                stream_mode="messages",
            ):
                token, chunk_metadata = chunk
                if (
                    isinstance(token, AIMessage)
                    and token.content
                    and chunk_metadata.get("langgraph_node") == "model"
                ):
                    content += token.content
            return content

    try:
        full_content = await _run_stream()
    except Exception as e:
        if "StructuredOutputValidationError" in str(e) or "Extra data" in str(e):
            logger.warning("StructuredOutputValidationError in stream, retrying: %s", e)
            full_content = await _run_stream()
        else:
            raise

    if full_content:
        yield _extract_answer(full_content), False, []

    # Read sources from checkpoint structured_response
    sources: list[str] = []
    structured = await _get_structured_response(thread_id)
    if structured:
        sources = structured.sources

    yield "", True, sources


@observe(as_type="agent")
async def run_orchestrator_agent(
    user_message: str,
    thread_id: str,
    document_ids: list[int] | None = None,
    metadata: dict | None = None,
    tracer_metadata: dict | None = None,
    run_id: str | None = None,
    on_stage: Callable[[str], None] | None = None,
    task_prompt: str | list[str] | None = None,
    recursion_limit: int = 30,
    include_document_abstracts: bool = True,
    max_searches: int | None = None,
    max_consecutive_empty: int | None = None,
    use_mini: bool = False,
) -> tuple[str, list[str]]:
    """Like run_agent but uses the orchestrator agent with agent_tools."""
    import uuid as _uuid
    from tracer import LocalTracer
    agent = _get_orchestrator_agent(mini=use_mini)
    metadata = {**(metadata or {}), **(tracer_metadata or {})}
    metadata = _attach_langfuse_prompt(metadata)
    with propagate_attributes(version=metadata.get("prompt_version")) if metadata.get("prompt_version") else contextlib.nullcontext():
        tracer = LocalTracer(
        thread_id=thread_id,
        document_ids=document_ids,
        mode=metadata.get("mode"),
        agent_name=metadata.get("agent_name"),
        prompt_name=metadata.get("prompt_name"),
        prompt_version=metadata.get("prompt_version"),
        base_prompt_name=metadata.get("base_prompt_name"),
        task_prompt_name=metadata.get("task_prompt_name"),
        quality_prompt_name=metadata.get("quality_prompt_name"),
        base_prompt_hash=metadata.get("base_prompt_hash"),
        task_prompt_hash=metadata.get("task_prompt_hash"),
        quality_prompt_hash=metadata.get("quality_prompt_hash"),
        prompt_stack_name=metadata.get("prompt_stack_name"),
        prompt_stack_json=metadata.get("prompt_stack_json"),
        prompt_stack_tokens=metadata.get("prompt_stack_tokens"),
        quality_score=metadata.get("quality_score"),
        user_feedback=metadata.get("user_feedback"),
        original_intent=metadata.get("original_intent"),
        resolved_intent=metadata.get("resolved_intent"),
    )
    langfuse_handler = CallbackHandler()
    config: dict = {"configurable": {"thread_id": thread_id}, "callbacks": [tracer, langfuse_handler], "recursion_limit": recursion_limit}
    config["metadata"] = metadata
    if run_id:
        config["run_id"] = _uuid.UUID(run_id)

    _ctx: AgentContext | None = None

    async def _invoke():
        nonlocal _ctx
        with propagate_attributes(session_id=thread_id):
            _ctx = AgentContext(
                document_ids=document_ids,
                on_stage=on_stage,
                mode=metadata.get("mode"),
                max_searches=max_searches,
                max_consecutive_empty=max_consecutive_empty,
                thread_id=thread_id,
                run_id=run_id,
            )
            return await agent.ainvoke(
                {"messages": _build_messages(user_message, document_ids, task_prompt, include_document_abstracts, thread_id=thread_id)},
                config,
                context=_ctx,
            )

    try:
        result = await _invoke()
    except Exception as e:
        if "StructuredOutputValidationError" in str(e) or "Extra data" in str(e):
            logger.warning("StructuredOutputValidationError on first attempt, retrying: %s", e)
            result = await _invoke()
        else:
            raise

    tool_sources: list[str] = _ctx.tool_sources if _ctx else []
    structured: AgentResponse | None = result.get("structured_response")
    if structured:
        merged = list(dict.fromkeys(structured.sources + tool_sources))
        return structured.answer, merged
    ai_messages = [m for m in result["messages"] if isinstance(m, AIMessage) and m.content]
    raw = ai_messages[-1].content if ai_messages else "Unable to generate a response."
    return _extract_answer(raw), tool_sources


@observe(as_type="agent")
async def run_orchestrator_agent_stream(
    user_message: str,
    thread_id: str,
    document_ids: list[int] | None = None,
    metadata: dict | None = None,
    tracer_metadata: dict | None = None,
    run_id: str | None = None,
    on_stage: Callable[[str], None] | None = None,
    task_prompt: str | list[str] | None = None,
    include_document_abstracts: bool = True,
    max_searches: int | None = None,
    max_consecutive_empty: int | None = None,
    use_mini: bool = False,
):
    """Like run_agent_stream but uses the orchestrator agent with agent_tools."""
    import uuid as _uuid
    from tracer import LocalTracer
    agent = _get_orchestrator_agent(mini=use_mini)
    metadata = {**(metadata or {}), **(tracer_metadata or {})}
    metadata = _attach_langfuse_prompt(metadata)
    with propagate_attributes(version=metadata.get("prompt_version")) if metadata.get("prompt_version") else contextlib.nullcontext():
        tracer = LocalTracer(
        thread_id=thread_id,
        document_ids=document_ids,
        mode=metadata.get("mode"),
        agent_name=metadata.get("agent_name"),
        prompt_name=metadata.get("prompt_name"),
        prompt_version=metadata.get("prompt_version"),
        base_prompt_name=metadata.get("base_prompt_name"),
        task_prompt_name=metadata.get("task_prompt_name"),
        quality_prompt_name=metadata.get("quality_prompt_name"),
        base_prompt_hash=metadata.get("base_prompt_hash"),
        task_prompt_hash=metadata.get("task_prompt_hash"),
        quality_prompt_hash=metadata.get("quality_prompt_hash"),
        prompt_stack_name=metadata.get("prompt_stack_name"),
        prompt_stack_json=metadata.get("prompt_stack_json"),
        prompt_stack_tokens=metadata.get("prompt_stack_tokens"),
        quality_score=metadata.get("quality_score"),
        user_feedback=metadata.get("user_feedback"),
        original_intent=metadata.get("original_intent"),
        resolved_intent=metadata.get("resolved_intent"),
    )
    langfuse_handler = CallbackHandler()
    config = {"configurable": {"thread_id": thread_id}, "callbacks": [tracer, langfuse_handler], "recursion_limit": 30, "metadata": metadata}
    if run_id:
        config["run_id"] = _uuid.UUID(run_id)

    _ctx: AgentContext | None = None

    async def _run_stream() -> str:
        nonlocal _ctx
        with propagate_attributes(session_id=thread_id):
            _ctx = AgentContext(
                document_ids=document_ids,
                on_stage=on_stage,
                mode=metadata.get("mode"),
                max_searches=max_searches,
                max_consecutive_empty=max_consecutive_empty,
                thread_id=thread_id,
                run_id=run_id,
            )
            content = ""
            async for chunk in agent.astream(
                {"messages": _build_messages(user_message, document_ids, task_prompt, include_document_abstracts, thread_id=thread_id)},
                config,
                context=_ctx,
                stream_mode="messages",
            ):
                token, chunk_metadata = chunk
                if (
                    isinstance(token, AIMessage)
                    and token.content
                    and chunk_metadata.get("langgraph_node") == "model"
                ):
                    content += token.content
            return content

    try:
        full_content = await _run_stream()
    except Exception as e:
        if "StructuredOutputValidationError" in str(e) or "Extra data" in str(e):
            logger.warning("StructuredOutputValidationError in orchestrator stream, retrying: %s", e)
            full_content = await _run_stream()
        else:
            raise

    if full_content:
        yield _extract_answer(full_content), False, []

    tool_sources: list[str] = _ctx.tool_sources if _ctx else []
    structured = await _get_structured_response(thread_id)
    sources = list(dict.fromkeys((structured.sources if structured else []) + tool_sources))
    yield "", True, sources
