from utils import new_id
from collections.abc import AsyncIterator
from datetime import datetime, timezone
import asyncio
import contextlib
import json
import logging
import uuid as _uuid
from typing import Callable

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import AzureChatOpenAI
from langfuse import observe, propagate_attributes

from config import settings
from observability import ainvoke_traced_generation, update_current_observation_io
from .no_tool_runner import run_no_tool_agent, stream_no_tool_agent, write_agent_span
from .request_context import get_user_id
from prompting.loader import load_stack

from .types import AgentResult, AgentStatus

STACK_NAME = "chat_default"
PROMPT_NAME = "chat_mode"
AGENT_NAME = "chat"

logger = logging.getLogger(__name__)


async def _emit_stage(on_stage, msg: str) -> None:
    if not on_stage:
        return
    try:
        result = on_stage(msg)
        if asyncio.iscoroutine(result):
            await result
    except Exception:
        pass


def trace_metadata() -> dict[str, str | int]:
    stack = load_stack(STACK_NAME)
    return {
        "agent_name": AGENT_NAME,
        **stack.metadata(),
    }


def _llm(use_mini: bool = False) -> AzureChatOpenAI:
    deployment = (
        settings.azure_mini_deployment
        if use_mini and settings.azure_mini_deployment
        else settings.azure_chat_deployment
    )
    return AzureChatOpenAI(
        azure_deployment=deployment,
        azure_endpoint=settings.azure_openai_endpoint,
        api_key=settings.azure_openai_api_key.get_secret_value(),
        api_version=settings.azure_openai_api_version,
        temperature=0.2,
    )


def _write_composition_trace(
    observation_id: str,
    thread_id: str,
    document_ids: list[int] | None,
    metadata: dict,
    inputs: dict,
    *,
    trace_id: str | None = None,
    output: dict | None = None,
    error: str | None = None,
) -> None:
    """Persist the router-controlled final composition step as a Trace v2 observation."""
    if trace_id:
        try:
            from services.trace_ingestion import TraceEventIngestor
            from datetime import datetime as _dt, timezone as _tz
            now_iso = _dt.now(_tz.utc).isoformat()
            TraceEventIngestor.enqueue_sync([{
                "event_type": "observation-create",
                "body": {
                    "observation_id": observation_id,
                    "trace_id": trace_id,
                    "type": "SPAN",
                    "name": "chat_agent.compose_final_response",
                    "start_time": now_iso,
                    "end_time": now_iso if (output or error) else None,
                    "input": inputs,
                    "output": output,
                    "status": "ERROR" if error else "DEFAULT",
                    "status_message": error,
                    "metadata": {k: metadata[k] for k in ("agent_name", "prompt_name") if k in metadata and metadata[k]},
                },
            }])
        except Exception:
            pass


@observe(as_type="agent", name="Compose Final Response")
async def compose_final_response(
    *,
    user_message: str,
    task_result: AgentResult,
    thread_id: str,
    document_ids: list[int] | None = None,
    observation_id: str | None = None,
    trace_id: str | None = None,
    use_mini: bool = False,
) -> AgentResult:
    """Format a task-agent result for the user without routing or tool access.

    This is intentionally separate from ``answer()``.  Both paths are no-tool
    chat calls; this one composes an already-computed task result and is meant
    for router-controlled plans.
    """
    observation_id = observation_id or new_id()
    stack = load_stack(STACK_NAME)
    metadata = {
        "agent_name": AGENT_NAME,
        **stack.metadata(),
    }
    inputs = {
        "user_message": user_message,
        "task_agent": task_result.agent_name,
        "task_answer": task_result.response,
        "sources": task_result.sources,
    }
    await asyncio.to_thread(
        _write_composition_trace, observation_id, thread_id, document_ids, metadata, inputs,
        trace_id=trace_id,
    )
    system_messages = [SystemMessage(content=content) for content in stack.contents]
    system_messages.append(SystemMessage(content=(
        "You are formatting the final user-facing response from an existing "
        "task-agent result. Do not perform routing. Do not add facts, evidence, "
        "or content that does not appear in the task-agent answer. "
        "If the task-agent answer says content was not found or is incomplete, "
        "preserve that incompleteness — do not fill gaps with your own knowledge. "
        "Preserve all technical terms, classification names, and taxonomy labels "
        "verbatim; never substitute them with synonyms or paraphrases."
    )))
    payload = {
        **inputs,
        "response_contract": {
            "language": "Match the user's language.",
            "preserve_sources": True,
            "do_not_add_new_facts": True,
        },
    }
    messages = [
        *system_messages,
        HumanMessage(content=json.dumps(payload, ensure_ascii=False)),
    ]
    try:
        with propagate_attributes(
            session_id=thread_id,
            user_id=get_user_id(),
            version=metadata.get("prompt_version"),
        ) if thread_id else contextlib.nullcontext():
            response = await ainvoke_traced_generation(
                _llm(use_mini=use_mini),
                messages,
                prompt_name=PROMPT_NAME,
                name="AzureChatOpenAI final-composition",
                metadata=metadata,
            )
        content = str(getattr(response, "content", response)).strip()
        output = {"answer": content, "sources": task_result.sources}
        await asyncio.to_thread(
            _write_composition_trace, observation_id, thread_id, document_ids, metadata, inputs,
            trace_id=trace_id, output=output,
        )
        return AgentResult(
            response=content,
            sources=task_result.sources,
            agent_name=AGENT_NAME,
            prompt_name=str(metadata.get("prompt_name", PROMPT_NAME)),
            prompt_version=str(metadata.get("prompt_version", "unknown")),
            observation_id=observation_id,
            status=AgentStatus(
                completed=True,
                work_summary="從對話 context 和文件摘要回答。" if document_ids else "從對話 context 回答（無文件）。",
            ),
        )
    except Exception as exc:
        _write_composition_trace(
            observation_id,
            thread_id,
            document_ids,
            metadata,
            inputs,
            trace_id=trace_id,
            error=str(exc),
        )
        raise


@observe(as_type="agent", name="Chat Agent", capture_input=False, capture_output=False)
async def answer(
    user_message: str,
    thread_id: str,
    document_ids: list[int] | None = None,
    *,
    observation_id: str | None = None,
    trace_id: str | None = None,
    on_stage: Callable[[str], None] | None = None,
    use_mini: bool = False,
) -> AgentResult:
    stack = load_stack(STACK_NAME)
    meta = {
        "agent_name": AGENT_NAME,
        **stack.metadata(),
    }
    await _emit_stage(on_stage, "組織回答中")
    from datetime import datetime, timezone as _tz
    import uuid as _uuid
    observation_id = observation_id or new_id()
    agent_start = datetime.now(_tz.utc)
    if trace_id:
        await write_agent_span(
            observation_id=observation_id,
            trace_id=trace_id,
            thread_id=thread_id,
            parent_observation_id=trace_id,
            name="Chat Agent",
            start_time=agent_start,
            input_data={"messages": [{"role": "user", "content": user_message}]},
            extra_metadata={"agent_name": AGENT_NAME},
        )
    response, sources, meta, observation_id = await run_no_tool_agent(
        user_message=user_message,
        thread_id=thread_id,
        document_ids=document_ids,
        stack_name=STACK_NAME,
        prompt_name=PROMPT_NAME,
        agent_name=AGENT_NAME,
        observation_id=observation_id,
        trace_id=trace_id,
        use_mini=use_mini,
        extra_system_messages=[
            "You do not have retrieval tools in this step. If the request "
            "requires document evidence, answer only from context already "
            "provided by the router or state that the router should use a "
            "retrieval/research route."
        ],
    )
    # Detect self-reported context insufficiency from chat_mode prompt
    _insufficient = "[INSUFFICIENT_CONTEXT]" in response
    response = response.replace("[INSUFFICIENT_CONTEXT]", "").strip()

    # Update Chat Agent SPAN with output and end time
    if trace_id:
        await write_agent_span(
            observation_id=observation_id,
            trace_id=trace_id,
            thread_id=thread_id,
            parent_observation_id=trace_id,
            name="Chat Agent",
            start_time=agent_start,
            end_time=datetime.now(_tz.utc),
            input_data={"messages": [{"role": "user", "content": user_message}]},
            output_data={"answer": response, "sources": sources},
            extra_metadata={"agent_name": AGENT_NAME},
        )
    update_current_observation_io(
        input={
            "message": user_message,
            "document_ids": document_ids or [],
        },
        output={"answer": response, "sources": sources},
        metadata={
            "agent_name": AGENT_NAME,
            "prompt_stack_name": meta.get("prompt_stack_name"),
            "prompt_name": meta.get("prompt_name"),
            "prompt_version": meta.get("prompt_version"),
        },
    )
    return AgentResult(
        response=response,
        sources=sources,
        agent_name=AGENT_NAME,
        prompt_name=str(meta.get("prompt_name", PROMPT_NAME)),
        prompt_version=str(meta.get("prompt_version", "unknown")),
        observation_id=observation_id,
        status=AgentStatus(
            completed=not _insufficient,
            work_summary="直接從對話 context 回答。",
            agent_limitation="chat 無文件搜尋工具，現有 context 不足以充分回答" if _insufficient else "",
        ),
    )


@observe(as_type="agent", name="Chat Agent", capture_input=False, capture_output=False)
async def stream(
    user_message: str,
    thread_id: str,
    document_ids: list[int] | None = None,
    *,
    observation_id: str | None = None,
    trace_id: str | None = None,
    on_stage: Callable[[str], None] | None = None,
    use_mini: bool = False,
) -> AsyncIterator[tuple[str, bool, list[str]]]:
    observation_id = observation_id or new_id()
    agent_start = datetime.now(timezone.utc)
    if trace_id:
        await write_agent_span(
            observation_id=observation_id,
            trace_id=trace_id,
            thread_id=thread_id,
            parent_observation_id=trace_id,
            name="Chat Agent",
            start_time=agent_start,
            input_data={"messages": [{"role": "user", "content": user_message}]},
            extra_metadata={"agent_name": AGENT_NAME},
        )
    await _emit_stage(on_stage, "組織回答中")
    content = ""
    async for token in stream_no_tool_agent(
        user_message=user_message,
        thread_id=thread_id,
        document_ids=document_ids,
        stack_name=STACK_NAME,
        prompt_name=PROMPT_NAME,
        agent_name=AGENT_NAME,
        observation_id=observation_id,
        trace_id=trace_id,
        use_mini=use_mini,
        extra_system_messages=[
            "You do not have retrieval tools in this step. If the request "
            "requires document evidence, answer only from context already "
            "provided by the router or state that the router should use a "
            "retrieval/research route."
        ],
    ):
        content += token
        yield token, False, []
    if trace_id:
        await write_agent_span(
            observation_id=observation_id,
            trace_id=trace_id,
            thread_id=thread_id,
            parent_observation_id=trace_id,
            name="Chat Agent",
            start_time=agent_start,
            end_time=datetime.now(timezone.utc),
            input_data={"messages": [{"role": "user", "content": user_message}]},
            output_data={"answer": content, "sources": []},
            extra_metadata={"agent_name": AGENT_NAME},
        )
    yield "", True, []
