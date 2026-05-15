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
from langfuse import observe, propagate_attributes
from .request_context import get_user_id

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
from observability import (
    langfuse_callbacks_from_metadata,
    langfuse_prompt_metadata_from_metadata,
)
logger = logging.getLogger(__name__)


def _get_abstracts(document_ids: list[int] | None) -> list[dict]:
    """Fetch abstract_text for the given documents from PostgreSQL."""
    from db import db_session, Document as DocModel
    with db_session() as db:
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


_checkpointer: AsyncPostgresSaver | None = None
_tool_agent = None
_tool_agent_mini = None

# Runtime prompt behavior now comes from prompting.loader stacks injected per
# request.  Keep the agent-level system prompt intentionally small so prompt
# hot reload works without rebuilding the LangGraph agent.
SYSTEM_PROMPT = "You are an AI research assistant. Follow the request-specific system messages."



class AgentResponse(BaseModel):
    answer: str = Field(description="Complete answer to the user's question")
    sources: list[str] = Field(
        default_factory=list,
        description='Most relevant sources referenced (max 3), each as "filename p.N" (e.g. "report.pdf p.3")',
        max_length=3,
    )



try:
    _llm = AzureChatOpenAI(
        azure_deployment=settings.azure_chat_deployment,
        azure_endpoint=settings.azure_openai_endpoint,
        api_key=settings.azure_openai_api_key.get_secret_value(),
        api_version=settings.azure_openai_api_version,
        temperature=0.3,
    )
    _agent_llm = _llm.bind(parallel_tool_calls=False)
    set_query_expander_llm(_llm)

    _mini_llm = AzureChatOpenAI(
        azure_deployment=settings.azure_mini_deployment,
        azure_endpoint=settings.azure_openai_endpoint,
        api_key=settings.azure_openai_api_key.get_secret_value(),
        api_version=settings.azure_openai_api_version,
        temperature=0.3,
    )
    _mini_agent_llm = _mini_llm.bind(parallel_tool_calls=False)
except Exception as _llm_init_err:
    logger.error(
        "Failed to initialise Azure OpenAI clients at startup: %s. "
        "Check AZURE_OPENAI_API_KEY / AZURE_OPENAI_ENDPOINT in .env.",
        _llm_init_err,
    )
    raise


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
    else:
        # No HumanMessage in the tail (e.g. long tool-call chain) — keep last 10
        tail = tail[-10:]
    return {"messages": tail}



async def setup_checkpointer():
    """Initialize AsyncPostgresSaver and build the agent. Called once at startup."""
    global _checkpointer, _tool_agent, _tool_agent_mini
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

    _tool_agent = _make_agent(_agent_llm, TOOLS)
    _tool_agent_mini = _make_agent(_mini_agent_llm, TOOLS)


def _get_tool_agent(mini: bool = False):
    return _tool_agent_mini if mini else _tool_agent


async def generate_title(messages: list[dict]) -> str:
    context = "\n".join(
        f"{m['role']}: {m['content'][:300]}" for m in messages[:4]
    )
    response = await _mini_llm.ainvoke([
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


def _get_conversation_context(thread_id: str) -> str | None:
    """Read the compressed context_summary for this conversation.

    Falls back to the latest research trace (pre-memory-service data) when
    context_summary has not been populated yet.
    """
    from services.memory_service import get_context_summary_text
    text = get_context_summary_text(thread_id)
    if text:
        return text
    # Fallback: query Trace table for conversations that predate context_summary
    from db import db_session, Trace
    import json as _json
    try:
        with db_session() as db:
            row = (
                db.query(Trace.display)
                .filter(
                    Trace.thread_id == thread_id,
                    Trace.task_type.in_(["research_task", "document_extraction"]),
                    Trace.display.isnot(None),
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
            return answer[:600] + ("…" if len(answer) > 600 else "")
    except Exception as _e:
        logger.debug("_get_conversation_context(%s): failed to parse trace display: %s", thread_id, _e)
        return None


def _build_messages(
    user_message: str,
    document_ids: list[int] | None,
    task_prompt: str | list[str] | None = None,
    include_document_abstracts: bool = True,
    thread_id: str | None = None,
    include_research_context: bool = True,
    long_term_memories: list[str] | None = None,
) -> list:
    """Build the message list for one agent turn.

    Injection order (all SystemMessages before the HumanMessage):
      1. task_prompt  ??per-request instructions (e.g. extraction rules)
      2. document abstracts ??background context for the referenced docs
      3. HumanMessage ??the actual user question / trigger
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
    if include_research_context and thread_id:
        research_ctx = _get_conversation_context(thread_id)
        if research_ctx:
            messages.append(SystemMessage(content=(
                "【對話記憶】以下是這段對話的研究摘要，可作為追問的參考背景，"
                "但仍以文件原文為準：\n"
                f"{research_ctx}"
            )))
    if long_term_memories:
        memory_block = "\n".join(long_term_memories)
        messages.append(SystemMessage(content=(
            "【歷史研究記憶】以下是你過去研究過的相關主題摘要，供參考：\n"
            f"{memory_block}"
        )))
    messages.append(HumanMessage(content=user_message))
    return messages


def _build_tracer(
    thread_id: str,
    document_ids: list[int] | None,
    metadata: dict,
    parent_run_id: str | None = None,
):
    from observability.tracer import LocalTracer
    return LocalTracer(
        thread_id=thread_id,
        document_ids=document_ids,
        task_type=metadata.get("task_type"),
        route_intent=metadata.get("route_intent"),
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
        primary_prompt_json=metadata.get("primary_prompt_json"),
        workflow_prompts_json=metadata.get("workflow_prompts_json"),
        prompt_stack_tokens=metadata.get("prompt_stack_tokens"),
        quality_score=metadata.get("quality_score"),
        user_feedback=metadata.get("user_feedback"),
        parent_run_id=parent_run_id,
        original_intent=metadata.get("original_intent"),
        resolved_intent=metadata.get("resolved_intent"),
    )


@observe(as_type="agent")
async def run_tool_agent(
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
    include_research_context: bool = True,
    max_searches: int | None = None,
    max_consecutive_empty: int | None = None,
    parent_run_id: str | None = None,
    use_mini: bool = False,
) -> tuple[str, list[str]]:
    import uuid as _uuid
    agent = _get_tool_agent(mini=use_mini)
    metadata = {**(metadata or {}), **(tracer_metadata or {})}
    with propagate_attributes(version=metadata.get("prompt_version")) if metadata.get("prompt_version") else contextlib.nullcontext():
        tracer = _build_tracer(thread_id, document_ids, metadata, parent_run_id)
    config_metadata = {**metadata, **langfuse_prompt_metadata_from_metadata(metadata)}
    config: dict = {
        "configurable": {"thread_id": thread_id},
        "callbacks": [tracer, *langfuse_callbacks_from_metadata(metadata)],
        "recursion_limit": recursion_limit,
    }
    config["metadata"] = config_metadata
    if run_id:
        config["run_id"] = _uuid.UUID(run_id)

    # Fetch long-term memories for chat/research when user is identified
    _long_term: list[str] = []
    if include_research_context and get_user_id():
        _intent = metadata.get("route_intent", "")
        if _intent in ("chat", "research"):
            try:
                from services.memory_service import search_research_memories
                _long_term = await search_research_memories(get_user_id(), user_message)
            except Exception as _mem_exc:
                logger.debug("Long-term memory search failed: %s", _mem_exc)

    async def _invoke():
        with propagate_attributes(session_id=thread_id, user_id=get_user_id()):
            return await agent.ainvoke(
                {"messages": _build_messages(user_message, document_ids, task_prompt, include_document_abstracts, thread_id=thread_id, include_research_context=include_research_context, long_term_memories=_long_term or None)},
                config,
                context=AgentContext(
                    document_ids=document_ids,
                    on_stage=on_stage,
                    task_type=metadata.get("task_type"),
                    route_intent=metadata.get("route_intent"),
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
async def run_tool_agent_stream(
    user_message: str,
    thread_id: str,
    document_ids: list[int] | None = None,
    metadata: dict | None = None,
    tracer_metadata: dict | None = None,
    run_id: str | None = None,
    on_stage: Callable[[str], None] | None = None,
    task_prompt: str | list[str] | None = None,
    include_document_abstracts: bool = True,
    include_research_context: bool = True,
    max_searches: int | None = None,
    max_consecutive_empty: int | None = None,
    parent_run_id: str | None = None,
    use_mini: bool = False,
):
    """Async generator yielding (token, is_done, sources) tuples."""
    import uuid as _uuid
    agent = _get_tool_agent(mini=use_mini)
    metadata = {**(metadata or {}), **(tracer_metadata or {})}
    with propagate_attributes(version=metadata.get("prompt_version")) if metadata.get("prompt_version") else contextlib.nullcontext():
        tracer = _build_tracer(thread_id, document_ids, metadata, parent_run_id)
    config_metadata = {**metadata, **langfuse_prompt_metadata_from_metadata(metadata)}
    config = {
        "configurable": {"thread_id": thread_id},
        "callbacks": [tracer, *langfuse_callbacks_from_metadata(metadata)],
        "recursion_limit": 30,
        "metadata": config_metadata,
    }
    if run_id:
        config["run_id"] = _uuid.UUID(run_id)

    async def _run_stream() -> str:
        with propagate_attributes(session_id=thread_id, user_id=get_user_id()):
            content = ""
            async for chunk in agent.astream(
                {"messages": _build_messages(user_message, document_ids, task_prompt, include_document_abstracts, thread_id=thread_id, include_research_context=include_research_context)},
                config,
                context=AgentContext(
                    document_ids=document_ids,
                    on_stage=on_stage,
                    task_type=metadata.get("task_type"),
                    route_intent=metadata.get("route_intent"),
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
