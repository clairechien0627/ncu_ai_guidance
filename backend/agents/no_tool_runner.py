"""No-tool LLM execution for agents that must not call retrieval tools."""

from __future__ import annotations
from utils import new_id

from datetime import datetime, timezone
import contextlib
import json
import logging
import uuid
from collections.abc import AsyncIterator

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import AzureChatOpenAI
from langfuse import propagate_attributes

from config import settings
from observability import ainvoke_traced_generation, astream_traced_generation
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


def write_agent_span(
    *,
    observation_id: str,
    trace_id: str,
    thread_id: str | None = None,
    parent_observation_id: str,
    name: str,
    start_time: datetime,
    end_time: datetime | None = None,
    input_data: dict | None = None,
    output_data: dict | None = None,
    error: str | None = None,
    extra_metadata: dict | None = None,
) -> None:
    """Write an agent-level SPAN observation. Called by each agent before running no_tool."""
    try:
        from services.trace_ingestion import TraceEventIngestor
        body: dict = {
            "observation_id": observation_id,
            "trace_id": trace_id,
            "thread_id": thread_id,
            "parent_observation_id": parent_observation_id,
            "type": "SPAN",
            "name": name,
            "start_time": start_time.isoformat(),
            "status": "ERROR" if error else "DEFAULT",
            "status_message": error,
        }
        if end_time:
            body["end_time"] = end_time.isoformat()
        if input_data:
            body["input"] = input_data
        if output_data:
            body["output"] = output_data
        if extra_metadata:
            body["metadata"] = extra_metadata
        TraceEventIngestor.enqueue_sync([{"event_type": "observation-create", "body": body}])
    except Exception as exc:
        logger.debug("write_agent_span enqueue failed (best-effort): %s", exc)


def _write_trace(
    *,
    observation_id: str,
    trace_id: str | None,
    thread_id: str,
    document_ids: list[int] | None,
    name: str,
    metadata: dict,
    inputs: dict,
    output: dict | None = None,
    error: str | None = None,
) -> None:
    # Agent SPAN and GENERATION observations are written through Trace v2 outbox
    # by write_agent_span() and the explicit GENERATION enqueue calls below.
    return


def _get_document_abstracts(document_ids: list[int]) -> list[dict]:
    from db import db_session
    from db.models import Document as DocModel
    with db_session() as db:
        rows = (
            db.query(DocModel.id, DocModel.filename, DocModel.abstract_text)
            .filter(DocModel.status == "ready", DocModel.id.in_(document_ids))
            .all()
        )
    return [{"filename": r.filename, "abstract": r.abstract_text} for r in rows if r.abstract_text]


def _prepare_no_tool_call(
    *,
    user_message: str,
    stack_name: str,
    agent_name: str,
    document_ids: list[int] | None = None,
    extra_system_messages: list[str] | None = None,
    payload: dict | None = None,
    sources: list[str] | None = None,
) -> tuple[list, dict, dict]:
    stack = load_stack(stack_name)
    metadata = {
        "agent_name": agent_name,
        **stack.metadata(),
    }

    inputs = {
        "user_message": user_message,
        "payload": payload or {},
        "sources": sources or [],
    }
    messages = [SystemMessage(content=content) for content in stack.contents]
    if document_ids:
        abstracts = _get_document_abstracts(document_ids)
        if abstracts:
            block = "\n\n".join(f"【{a['filename']}】\n{a['abstract']}" for a in abstracts)
            messages.append(SystemMessage(content=(
                "以下是本次對話引用的文件摘要，請以此作為背景資訊回答問題：\n\n" + block
            )))
    for content in extra_system_messages or []:
        if content:
            messages.append(SystemMessage(content=content))
    messages.append(HumanMessage(content=json.dumps({
        "user_message": user_message,
        **(payload or {}),
    }, ensure_ascii=False)))
    return messages, metadata, inputs


async def run_no_tool_agent(
    *,
    user_message: str,
    thread_id: str,
    document_ids: list[int] | None,
    stack_name: str,
    prompt_name: str,
    agent_name: str,
    observation_id: str | None = None,
    trace_id: str | None = None,
    use_mini: bool = False,
    extra_system_messages: list[str] | None = None,
    payload: dict | None = None,
    sources: list[str] | None = None,
) -> tuple[str, list[str], dict, str]:
    """Run one no-tool LLM call and persist a local trace row."""
    observation_id = observation_id or new_id()
    messages, metadata, inputs = _prepare_no_tool_call(
        user_message=user_message,
        stack_name=stack_name,
        agent_name=agent_name,
        document_ids=document_ids,
        extra_system_messages=extra_system_messages,
        payload=payload,
        sources=sources,
    )
    _write_trace(
        observation_id=observation_id,
        trace_id=trace_id,
        thread_id=thread_id,
        document_ids=document_ids,
        name=metadata.get("agent_name", agent_name).replace("_", " ").title(),
        metadata=metadata,
        inputs=inputs,
    )

    try:
        llm_start = datetime.now(timezone.utc)
        _llm_instance = _llm(use_mini=use_mini)
        with propagate_attributes(
            session_id=thread_id,
            user_id=get_user_id(),
            version=metadata.get("prompt_version"),
        ) if thread_id else contextlib.nullcontext():
            response = await ainvoke_traced_generation(
                _llm_instance,
                messages,
                prompt_name=prompt_name,
                name=f"AzureChatOpenAI {agent_name}",
                metadata=metadata,
            )
        llm_end = datetime.now(timezone.utc)
        content = str(getattr(response, "content", response)).strip()
        result_sources = sources or []
        output = {"answer": content, "sources": result_sources}

        token_usage = getattr(response, "response_metadata", {}).get("token_usage", {})
        if not token_usage:
            token_usage = getattr(response, "usage_metadata", {}) or {}
        prompt_tokens = token_usage.get("prompt_tokens") or token_usage.get("input_tokens")
        completion_tokens = token_usage.get("completion_tokens") or token_usage.get("output_tokens")
        if trace_id and (prompt_tokens or completion_tokens):
            try:
                from services.trace_ingestion import TraceEventIngestor as _TEI
                _TEI.enqueue_sync([{
                    "event_type": "observation-create",
                    "body": {
                        "observation_id": new_id(),
                        "trace_id": trace_id,
                        "thread_id": thread_id,
                        "parent_observation_id": observation_id,
                        "type": "GENERATION",
                        "name": f"AzureChatOpenAI {agent_name}",
                        "model": settings.azure_mini_deployment if use_mini else settings.azure_chat_deployment,
                        "model_parameters": {"temperature": _llm_instance.temperature},
                        "prompt_tokens": prompt_tokens,
                        "completion_tokens": completion_tokens,
                        "total_tokens": (prompt_tokens or 0) + (completion_tokens or 0),
                        "start_time": llm_start.isoformat(),
                        "end_time": llm_end.isoformat(),
                        "status": "DEFAULT",
                        "metadata": {"prompt_name": prompt_name},
                    },
                }])
            except Exception as exc:
                logger.debug("GENERATION observation enqueue failed (best-effort): %s", exc)

        _write_trace(
            observation_id=observation_id,
            trace_id=trace_id,
            thread_id=thread_id,
            document_ids=document_ids,
            name=metadata.get("agent_name", agent_name).replace("_", " ").title(),
            metadata=metadata,
            inputs=inputs,
            output=output,
        )
        return content, result_sources, metadata, observation_id
    except Exception as exc:
        _write_trace(
            observation_id=observation_id,
            trace_id=trace_id,
            thread_id=thread_id,
            document_ids=document_ids,
            name=metadata.get("agent_name", agent_name).replace("_", " ").title(),
            metadata=metadata,
            inputs=inputs,
            error=str(exc),
        )
        raise


async def stream_no_tool_agent(
    *,
    user_message: str,
    thread_id: str,
    document_ids: list[int] | None,
    stack_name: str,
    prompt_name: str,
    agent_name: str,
    observation_id: str | None = None,
    trace_id: str | None = None,
    use_mini: bool = False,
    extra_system_messages: list[str] | None = None,
    payload: dict | None = None,
    sources: list[str] | None = None,
) -> AsyncIterator[str]:
    """Stream one no-tool LLM call token-by-token and persist a local trace row."""
    observation_id = observation_id or new_id()
    messages, metadata, inputs = _prepare_no_tool_call(
        user_message=user_message,
        stack_name=stack_name,
        agent_name=agent_name,
        document_ids=document_ids,
        extra_system_messages=extra_system_messages,
        payload=payload,
        sources=sources,
    )
    _write_trace(
        observation_id=observation_id,
        trace_id=trace_id,
        thread_id=thread_id,
        document_ids=document_ids,
        name=metadata.get("agent_name", agent_name).replace("_", " ").title(),
        metadata=metadata,
        inputs=inputs,
    )

    content = ""
    stream_start = datetime.now(timezone.utc)
    completion_start_time: datetime | None = None
    try:
        llm_instance = _llm(use_mini=use_mini)
        with propagate_attributes(
            session_id=thread_id,
            user_id=get_user_id(),
            version=metadata.get("prompt_version"),
        ) if thread_id else contextlib.nullcontext():
            async for token_text in astream_traced_generation(
                llm_instance, messages,
                prompt_name=prompt_name,
                name=f"AzureChatOpenAI {agent_name}",
                metadata=metadata,
            ):
                if completion_start_time is None:
                    completion_start_time = datetime.now(timezone.utc)
                content += token_text
                yield token_text
        stream_end = datetime.now(timezone.utc)
        result_sources = sources or []
        _write_trace(
            observation_id=observation_id,
            trace_id=trace_id,
            thread_id=thread_id,
            document_ids=document_ids,
            name=metadata.get("agent_name", agent_name).replace("_", " ").title(),
            metadata=metadata,
            inputs=inputs,
            output={"answer": content.strip(), "sources": result_sources},
        )
        if trace_id:
            try:
                from services.trace_ingestion import TraceEventIngestor as _TEI
                _TEI.enqueue_sync([{
                    "event_type": "observation-create",
                    "body": {
                        "observation_id": new_id(),
                        "trace_id": trace_id,
                        "thread_id": thread_id,
                        "parent_observation_id": observation_id,
                        "type": "GENERATION",
                        "name": f"AzureChatOpenAI {agent_name}",
                        "model": settings.azure_mini_deployment if use_mini else settings.azure_chat_deployment,
                        "model_parameters": {"temperature": llm_instance.temperature},
                        "start_time": stream_start.isoformat(),
                        "completion_start_time": completion_start_time.isoformat() if completion_start_time else None,
                        "end_time": stream_end.isoformat(),
                        "status": "DEFAULT",
                        "metadata": {"prompt_name": prompt_name},
                    },
                }])
            except Exception as exc:
                logger.debug("streaming GENERATION observation enqueue failed (best-effort): %s", exc)
    except Exception as exc:
        _write_trace(
            observation_id=observation_id,
            trace_id=trace_id,
            thread_id=thread_id,
            document_ids=document_ids,
            name=metadata.get("agent_name", agent_name).replace("_", " ").title(),
            metadata=metadata,
            inputs=inputs,
            error=str(exc),
        )
        raise
