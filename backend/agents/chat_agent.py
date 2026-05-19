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
from .no_tool_runner import run_no_tool_agent, stream_no_tool_agent
from .request_context import get_user_id
from prompting.loader import load_stack

from .types import AgentResult

STACK_NAME = "chat_default"
PROMPT_NAME = "chat_mode"
AGENT_NAME = "chat_agent"
COMPOSITION_TASK_TYPE = "response_composition"

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
        "task_type": "chat_turn",
        "route_intent": "chat",
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
    run_id: str,
    thread_id: str,
    document_ids: list[int] | None,
    metadata: dict,
    inputs: dict,
    *,
    parent_run_id: str | None = None,
    output: dict | None = None,
    error: str | None = None,
) -> None:
    """Persist the future router-controlled final composition step locally."""
    import os
    # Write observation to v2 when this run is a child of a router trace
    if parent_run_id:
        try:
            from services.trace_ingestion import TraceEventIngestor
            from datetime import datetime as _dt, timezone as _tz
            now_iso = _dt.now(_tz.utc).isoformat()
            TraceEventIngestor.enqueue_sync([{
                "event_type": "observation-create",
                "body": {
                    "observation_id": run_id,
                    "trace_id": parent_run_id,
                    "type": "SPAN",
                    "name": "chat_agent.compose_final_response",
                    "start_time": now_iso,
                    "end_time": now_iso if (output or error) else None,
                    "input": inputs,
                    "output": output,
                    "level": "ERROR" if error else "DEFAULT",
                    "status_message": error,
                    "metadata": {k: metadata[k] for k in ("agent_name", "task_type", "prompt_name") if k in metadata and metadata[k]},
                },
            }])
        except Exception:
            pass

    if os.environ.get("LEGACY_TRACE_WRITE", "true").lower() in ("0", "false", "no"):
        return

    from db import db_session, Trace

    db = None
    try:
        with db_session() as db:
            now = datetime.now(timezone.utc)
            trace = db.query(Trace).filter(Trace.run_id == run_id).first()
            if trace is None:
                trace = Trace(
                    run_id=run_id,
                    parent_run_id=parent_run_id,
                    run_type="llm",
                    name="chat_agent.compose_final_response",
                    start_time=now,
                    thread_id=thread_id,
                    document_ids=json.dumps(document_ids) if document_ids else None,
                    task_type=COMPOSITION_TASK_TYPE,
                    route_intent="chat",
                    agent_name=AGENT_NAME,
                    prompt_name=metadata.get("prompt_name"),
                    prompt_version=metadata.get("prompt_version"),
                    base_prompt_name=metadata.get("base_prompt_name"),
                    task_prompt_name=metadata.get("task_prompt_name"),
                    base_prompt_hash=metadata.get("base_prompt_hash"),
                    task_prompt_hash=metadata.get("task_prompt_hash"),
                    prompt_stack_name=metadata.get("prompt_stack_name"),
                    prompt_stack_json=metadata.get("prompt_stack_json"),
                    primary_prompt_json=metadata.get("primary_prompt_json"),
                    workflow_prompts_json=metadata.get("workflow_prompts_json"),
                    prompt_stack_tokens=metadata.get("prompt_stack_tokens"),
                    inputs=json.dumps(inputs, ensure_ascii=False),
                )
                db.add(trace)
            if output is not None:
                trace.outputs = json.dumps(output, ensure_ascii=False)
                trace.display = json.dumps(output, ensure_ascii=False)
                trace.end_time = now
            if error:
                trace.error = error
                trace.end_time = now
            db.commit()
    except Exception as exc:
        logger.warning("Composition trace write failed: %s", exc)
        if db is not None:
            db.rollback()


@observe(as_type="agent", name="Compose Final Response")
async def compose_final_response(
    *,
    user_message: str,
    task_result: AgentResult,
    thread_id: str,
    document_ids: list[int] | None = None,
    run_id: str | None = None,
    parent_run_id: str | None = None,
    use_mini: bool = False,
) -> AgentResult:
    """Format a task-agent result for the user without routing or tool access.

    This is intentionally separate from ``answer()``.  Both paths are no-tool
    chat calls; this one composes an already-computed task result and is meant
    for router-controlled plans.
    """
    run_id = run_id or str(_uuid.uuid4())
    stack = load_stack(STACK_NAME)
    metadata = {
        "task_type": COMPOSITION_TASK_TYPE,
        "route_intent": "chat",
        "agent_name": AGENT_NAME,
        **stack.metadata(),
    }
    inputs = {
        "user_message": user_message,
        "task_agent": task_result.agent_name,
        "task_type": task_result.task_type,
        "task_route_intent": task_result.route_intent,
        "task_answer": task_result.response,
        "sources": task_result.sources,
    }
    await asyncio.to_thread(
        _write_composition_trace, run_id, thread_id, document_ids, metadata, inputs,
        parent_run_id=parent_run_id,
    )
    system_messages = [SystemMessage(content=content) for content in stack.contents]
    system_messages.append(SystemMessage(content=(
        "You are formatting the final user-facing response from an existing "
        "task-agent result. Do not perform routing. Do not claim new evidence. "
        "Preserve important uncertainty, citations, and constraints from the "
        "task-agent answer."
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
            _write_composition_trace, run_id, thread_id, document_ids, metadata, inputs,
            parent_run_id=parent_run_id, output=output,
        )
        return AgentResult(
            response=content,
            sources=task_result.sources,
            task_type=COMPOSITION_TASK_TYPE,
            route_intent="chat",
            agent_name=AGENT_NAME,
            prompt_name=str(metadata.get("prompt_name", PROMPT_NAME)),
            prompt_version=str(metadata.get("prompt_version", "unknown")),
            trace_run_id=run_id,
            next_intent=None,
        )
    except Exception as exc:
        _write_composition_trace(
            run_id,
            thread_id,
            document_ids,
            metadata,
            inputs,
            parent_run_id=parent_run_id,
            error=str(exc),
        )
        raise


@observe(as_type="agent", name="Chat Agent", capture_input=False, capture_output=False)
async def answer(
    user_message: str,
    thread_id: str,
    document_ids: list[int] | None = None,
    *,
    run_id: str | None = None,
    parent_run_id: str | None = None,
    on_stage: Callable[[str], None] | None = None,
    use_mini: bool = False,
    original_intent: str | None = None,
    resolved_intent: str | None = None,
) -> AgentResult:
    stack = load_stack(STACK_NAME)
    meta = {
        "task_type": "chat_turn",
        "route_intent": "chat",
        "agent_name": AGENT_NAME,
        **stack.metadata(),
    }
    if original_intent:
        meta["original_intent"] = original_intent
    if resolved_intent:
        meta["resolved_intent"] = resolved_intent
    await _emit_stage(on_stage, "chat_agent: composing")
    response, sources, meta, actual_run_id = await run_no_tool_agent(
        user_message=user_message,
        thread_id=thread_id,
        document_ids=document_ids,
        stack_name=STACK_NAME,
        prompt_name=PROMPT_NAME,
        agent_name=AGENT_NAME,
        task_type="chat_turn",
        route_intent="chat",
        run_id=run_id,
        parent_run_id=parent_run_id,
        use_mini=use_mini,
        extra_system_messages=[
            "You do not have retrieval tools in this step. If the request "
            "requires document evidence, answer only from context already "
            "provided by the router or state that the router should use a "
            "retrieval/research route."
        ],
        original_intent=original_intent,
        resolved_intent=resolved_intent,
    )
    update_current_observation_io(
        input={
            "message": user_message,
            "document_ids": document_ids or [],
            "route_intent": "chat",
        },
        output={"answer": response, "sources": sources},
        metadata={
            "agent_name": AGENT_NAME,
            "task_type": "chat_turn",
            "prompt_stack_name": meta.get("prompt_stack_name"),
            "prompt_name": meta.get("prompt_name"),
            "prompt_version": meta.get("prompt_version"),
        },
    )
    return AgentResult(
        response=response,
        sources=sources,
        task_type="chat_turn",
        route_intent="chat",
        agent_name=AGENT_NAME,
        prompt_name=str(meta.get("prompt_name", PROMPT_NAME)),
        prompt_version=str(meta.get("prompt_version", "unknown")),
        trace_run_id=actual_run_id,
        next_intent=None,
    )


async def stream(
    user_message: str,
    thread_id: str,
    document_ids: list[int] | None = None,
    *,
    run_id: str | None = None,
    parent_run_id: str | None = None,
    on_stage: Callable[[str], None] | None = None,
    use_mini: bool = False,
    original_intent: str | None = None,
    resolved_intent: str | None = None,
) -> AsyncIterator[tuple[str, bool, list[str]]]:
    await _emit_stage(on_stage, "chat_agent: composing")
    async for token in stream_no_tool_agent(
        user_message=user_message,
        thread_id=thread_id,
        document_ids=document_ids,
        stack_name=STACK_NAME,
        prompt_name=PROMPT_NAME,
        agent_name=AGENT_NAME,
        task_type="chat_turn",
        route_intent="chat",
        run_id=run_id,
        parent_run_id=parent_run_id,
        use_mini=use_mini,
        extra_system_messages=[
            "You do not have retrieval tools in this step. If the request "
            "requires document evidence, answer only from context already "
            "provided by the router or state that the router should use a "
            "retrieval/research route."
        ],
        original_intent=original_intent,
        resolved_intent=resolved_intent,
    ):
        yield token, False, []
    yield "", True, []
