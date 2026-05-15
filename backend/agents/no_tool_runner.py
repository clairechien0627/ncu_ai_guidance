"""No-tool LLM execution for agents that must not call retrieval tools."""

from __future__ import annotations

from datetime import datetime, timezone
import contextlib
import json
import logging
import uuid

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import AzureChatOpenAI
from langfuse import propagate_attributes

from config import settings
from observability import ainvoke_traced_generation
from prompting.loader import load_stack

from .request_context import get_user_id

logger = logging.getLogger(__name__)


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


def _write_trace(
    *,
    run_id: str,
    parent_run_id: str | None,
    thread_id: str,
    document_ids: list[int] | None,
    name: str,
    metadata: dict,
    inputs: dict,
    output: dict | None = None,
    error: str | None = None,
) -> None:
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
                    name=name,
                    start_time=now,
                    thread_id=thread_id,
                    document_ids=json.dumps(document_ids) if document_ids else None,
                    task_type=metadata.get("task_type"),
                    route_intent=metadata.get("route_intent"),
                    agent_name=metadata.get("agent_name"),
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
                    original_intent=metadata.get("original_intent"),
                    resolved_intent=metadata.get("resolved_intent"),
                    inputs=json.dumps(inputs, ensure_ascii=False),
                    tool_count=0,
                    llm_call_count=1,
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
        logger.warning("No-tool trace write failed: %s", exc)
        if db is not None:
            db.rollback()


async def run_no_tool_agent(
    *,
    user_message: str,
    thread_id: str,
    document_ids: list[int] | None,
    stack_name: str,
    prompt_name: str,
    agent_name: str,
    task_type: str,
    route_intent: str,
    run_id: str | None = None,
    parent_run_id: str | None = None,
    use_mini: bool = False,
    extra_system_messages: list[str] | None = None,
    payload: dict | None = None,
    sources: list[str] | None = None,
    original_intent: str | None = None,
    resolved_intent: str | None = None,
) -> tuple[str, list[str], dict, str]:
    """Run one no-tool LLM call and persist a local trace row."""
    run_id = run_id or str(uuid.uuid4())
    stack = load_stack(stack_name)
    metadata = {
        "task_type": task_type,
        "route_intent": route_intent,
        "agent_name": agent_name,
        **stack.metadata(),
    }
    if original_intent:
        metadata["original_intent"] = original_intent
    if resolved_intent:
        metadata["resolved_intent"] = resolved_intent

    inputs = {
        "user_message": user_message,
        "payload": payload or {},
        "sources": sources or [],
    }
    _write_trace(
        run_id=run_id,
        parent_run_id=parent_run_id,
        thread_id=thread_id,
        document_ids=document_ids,
        name=f"{agent_name}.no_tool",
        metadata=metadata,
        inputs=inputs,
    )

    messages = [SystemMessage(content=content) for content in stack.contents]
    for content in extra_system_messages or []:
        if content:
            messages.append(SystemMessage(content=content))
    messages.append(HumanMessage(content=json.dumps({
        "user_message": user_message,
        **(payload or {}),
    }, ensure_ascii=False)))

    try:
        with propagate_attributes(
            session_id=thread_id,
            user_id=get_user_id(),
            version=metadata.get("prompt_version"),
        ) if thread_id else contextlib.nullcontext():
            response = await ainvoke_traced_generation(
                _llm(use_mini=use_mini),
                messages,
                prompt_name=prompt_name,
                name=f"AzureChatOpenAI {agent_name}",
                metadata=metadata,
            )
        content = str(getattr(response, "content", response)).strip()
        result_sources = sources or []
        output = {"answer": content, "sources": result_sources}
        _write_trace(
            run_id=run_id,
            parent_run_id=parent_run_id,
            thread_id=thread_id,
            document_ids=document_ids,
            name=f"{agent_name}.no_tool",
            metadata=metadata,
            inputs=inputs,
            output=output,
        )
        return content, result_sources, metadata, run_id
    except Exception as exc:
        _write_trace(
            run_id=run_id,
            parent_run_id=parent_run_id,
            thread_id=thread_id,
            document_ids=document_ids,
            name=f"{agent_name}.no_tool",
            metadata=metadata,
            inputs=inputs,
            error=str(exc),
        )
        raise
