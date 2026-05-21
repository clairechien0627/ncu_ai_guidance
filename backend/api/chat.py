import asyncio
import json
import logging
import uuid
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Optional

from utils import new_id

logger = logging.getLogger(__name__)

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from agents.runner import (
    generate_title,
    get_thread_messages,
    get_pending_interrupt,
    run_tool_agent_resume_stream,
)
from agents.router_agent import (
    run_research_agent,
    route_request,
    route_agent_message,
    route_agent_stream,
)
import agents.steering as steering
from agents.request_context import set_user_id
from api.dependencies import get_current_user
from db import get_db, Conversation, Document, Trace
from db.models import TraceV2
from db.models import User
from services.quota_service import check_quota

router = APIRouter(dependencies=[Depends(get_current_user)])


def _json_dumps(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False, default=str)


def _json_safe(data):
    return json.loads(json.dumps(data, ensure_ascii=False, default=str))


def _interrupt_event(interrupts) -> dict:
    first = interrupts[0] if interrupts else None
    return {
        "interrupt": _json_safe(getattr(first, "value", first) if first is not None else {}),
        "interrupt_id": getattr(first, "id", None) if first is not None else None,
    }


class ChatRequest(BaseModel):
    message: str
    thread_id: Optional[str] = None
    model: str = "gemini"
    document_ids: Optional[list[int]] = None
    bypass_cache: bool = False


def _get_previous_agent(db, thread_id: str | None) -> str | None:
    """Read the last agent used in this conversation from Conversation.last_agent_name."""
    if not thread_id:
        return None
    row = db.query(Conversation.last_agent_name).filter(
        Conversation.thread_id == thread_id
    ).first()
    return row[0] if row else None


def _should_use_mini(model: str, agent_name: str, document_ids: list[int] | None) -> bool:
    """Use the mini model when explicitly requested OR for simple chat without documents."""
    if "mini" in (model or "").lower():
        return True
    # Pure conversation (no documents, chat agent) → mini is sufficient
    if agent_name == "chat_agent" and not document_ids:
        return True
    return False


def _document_attachment_payload(db: Session, document_ids: list[int] | None) -> list[dict]:
    if not document_ids:
        return []
    docs = db.query(Document.id, Document.filename).filter(Document.id.in_(document_ids)).all()
    names = {doc_id: filename for doc_id, filename in docs}
    return [
        {"id": doc_id, "name": names.get(doc_id, f"Document #{doc_id}")}
        for doc_id in document_ids
    ]


def _hydrate_message_attachments(thread_id: str, messages: list[dict], db: Session) -> list[dict]:
    """Attach document chips to user messages using root trace document_ids."""
    traces = (
        db.query(Trace.document_ids)
        .filter(
            Trace.agent_name == "router_agent",
            Trace.thread_id == thread_id,
            Trace.document_ids.isnot(None),
        )
        .order_by(Trace.start_time.asc())
        .all()
    )
    attachments_by_turn = []
    for (raw_doc_ids,) in traces:
        try:
            doc_ids = json.loads(raw_doc_ids)
        except Exception:
            doc_ids = None
        attachments_by_turn.append(_document_attachment_payload(db, doc_ids if isinstance(doc_ids, list) else None))

    hydrated = []
    user_turn = 0
    for msg in messages:
        item = dict(msg)
        if item.get("role") == "user":
            if user_turn < len(attachments_by_turn) and attachments_by_turn[user_turn]:
                item["attached_docs"] = attachments_by_turn[user_turn]
            user_turn += 1
        hydrated.append(item)
    return hydrated


def _hydrate_assistant_meta(thread_id: str, messages: list[dict], db: Session) -> list[dict]:
    """Attach route/trace metadata to assistant messages using router-level traces by turn."""
    traces = (
        db.query(
            Trace.observation_id,
            Trace.task_type,
            Trace.agent_name,
            Trace.prompt_name,
            Trace.prompt_version,
        )
        .filter(Trace.agent_name == "router_agent", Trace.thread_id == thread_id)
        .order_by(Trace.start_time.asc())
        .all()
    )
    # Supplement with routed agent_name from TraceV2 metadata
    v2_rows = (
        db.query(TraceV2.trace_id, TraceV2.metadata_json)
        .filter(TraceV2.name == "router_agent", TraceV2.thread_id == thread_id)
        .order_by(TraceV2.start_time.asc())
        .all()
    )
    v2_agent_by_trace: dict[str, str] = {}
    for row in v2_rows:
        if row.metadata_json:
            meta = row.metadata_json if isinstance(row.metadata_json, dict) else json.loads(row.metadata_json)
            if meta.get("agent_name"):
                v2_agent_by_trace[row.trace_id] = meta["agent_name"]

    hydrated = []
    assistant_turn = 0
    for msg in messages:
        item = dict(msg)
        if item.get("role") == "assistant":
            if assistant_turn < len(traces):
                observation_id, task_type, agent_name, prompt_name, prompt_version = traces[assistant_turn]
                if task_type:
                    item["task_type"] = task_type
                routed = v2_agent_by_trace.get(observation_id or "") or agent_name
                if routed:
                    item["agent_name"] = routed
                if prompt_name:
                    item["prompt_name"] = prompt_name
                if prompt_version:
                    item["prompt_version"] = prompt_version
                if observation_id:
                    item["trace_id"] = observation_id
            assistant_turn += 1
        hydrated.append(item)
    return hydrated


@router.post("/api/chat")
async def chat(req: ChatRequest, db: Session = Depends(get_db),
               current_user: User = Depends(get_current_user)):
    def _setup_conv():
        if req.thread_id:
            c = db.query(Conversation).filter(
                Conversation.thread_id == req.thread_id,
                Conversation.user_id == str(current_user.id),
            ).first()
            if not c:
                raise HTTPException(status_code=404, detail="Conversation not found")
            c.model = req.model
            return c
        c = Conversation(model=req.model, thread_id=new_id(), user_id=str(current_user.id))
        db.add(c)
        db.commit()
        db.refresh(c)
        return c

    conv = await asyncio.to_thread(_setup_conv)
    check_quota(current_user, db)
    set_user_id(str(current_user.id))
    try:
        prev_agent = await asyncio.to_thread(_get_previous_agent, db, conv.thread_id if req.thread_id else None)
        route = await route_request(req.message, req.document_ids, conv.thread_id, previous_agent_name=prev_agent)
        trace_id = new_id()
        use_mini = _should_use_mini(req.model, route.agent_name, req.document_ids)
        result = await route_agent_message(
            req.message,
            conv.thread_id,
            req.document_ids,
            route=route,
            trace_id=trace_id,
            _hop_count=0,
            use_mini=use_mini,
        )
        response, sources = result.response, result.sources
    except Exception as e:
        logger.error("Agent error in chat endpoint", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal agent error")

    def _update_conv():
        conv.message_count = (conv.message_count or 0) + 2
        conv.last_agent_name = result.agent_name
        db.commit()
    await asyncio.to_thread(_update_conv)
    return {
        "thread_id": conv.thread_id,
        "response": response,
        "sources": sources,
        "task_type": result.task_type,
        "agent_name": result.agent_name,
        "prompt_name": result.prompt_name,
        "prompt_version": result.prompt_version,
        "trace_id": trace_id,
        "agent_observation_id": result.observation_id,
    }


@router.post("/api/chat/stream")
async def chat_stream(req: ChatRequest, db: Session = Depends(get_db),
                      current_user: User = Depends(get_current_user)):
    def _setup_conv():
        if req.thread_id:
            c = db.query(Conversation).filter(
                Conversation.thread_id == req.thread_id,
                Conversation.user_id == str(current_user.id),
            ).first()
            if not c:
                raise HTTPException(status_code=404, detail="Conversation not found")
            c.model = req.model
            return c
        c = Conversation(model=req.model, thread_id=new_id(), user_id=str(current_user.id))
        db.add(c)
        db.commit()
        db.refresh(c)
        return c

    conv = await asyncio.to_thread(_setup_conv)
    check_quota(current_user, db)

    # ── Mid-run steering detection ─────────────────────────────────────────────
    _STREAM_TTL = timedelta(minutes=10)
    _now = datetime.now(timezone.utc)
    if conv.stream_started_at is not None:
        started = conv.stream_started_at
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        if _now - started < _STREAM_TTL:
            steering.set(conv.thread_id, req.message)
            async def _queued_stream():
                yield f"data: {_json_dumps({'token': '您的補充已收到，將在下一步驟納入考量。', 'done': True, 'thread_id': conv.thread_id})}\n\n"
            return StreamingResponse(_queued_stream(), media_type="text/event-stream")

    def _set_stream_lock():
        conv.stream_started_at = datetime.now(timezone.utc)
        db.commit()
    await asyncio.to_thread(_set_stream_lock)

    is_new = req.thread_id is None
    set_user_id(str(current_user.id))

    prev_agent = await asyncio.to_thread(_get_previous_agent, db, conv.thread_id if req.thread_id else None)
    route = await route_request(req.message, req.document_ids, conv.thread_id, previous_agent_name=prev_agent)
    trace_id = new_id()
    use_mini = _should_use_mini(req.model, route.agent_name, req.document_ids)

    async def event_stream():
        sources = []
        full_response = ""
        route_payload = {
            "task_type": "chat_turn",
            "agent_name": route.agent_name,
            "prompt_name": route.prompt_name,
            "prompt_version": route.prompt_version,
            "trace_id": trace_id,
            "agent_observation_id": None,
        }
        try:
            yield f"data: {_json_dumps({'thread_id': conv.thread_id, **route_payload})}\n\n"
            observation_id = new_id()
            try:
                if route.agent_name == "research_agent":
                    # Merged event queue: ("token", t) | ("stage", msg) | None (sentinel on done)
                    event_queue: asyncio.Queue = asyncio.Queue()

                    def _push_stage(msg: str) -> None:
                        event_queue.put_nowait(("stage", msg))

                    def _push_token(t: str) -> None:
                        event_queue.put_nowait(("token", t))

                    task = asyncio.create_task(
                        run_research_agent(
                            req.message, conv.thread_id, req.document_ids,
                            observation_id=observation_id,
                            trace_id=trace_id,
                            on_stage=_push_stage,
                            on_token=_push_token,
                            bypass_cache=req.bypass_cache,
                        )
                    )
                    task.add_done_callback(lambda _: event_queue.put_nowait(None))

                    streamed_tokens = False
                    while True:
                        event = await event_queue.get()
                        if event is None:
                            # sentinel: drain any events that arrived simultaneously
                            while not event_queue.empty():
                                remaining = event_queue.get_nowait()
                                if remaining is None:
                                    continue
                                etype, val = remaining
                                if etype == "token":
                                    yield f"data: {_json_dumps({'token': val})}\n\n"
                                    streamed_tokens = True
                                else:
                                    yield f"data: {_json_dumps({'stage': val})}\n\n"
                            break
                        etype, val = event
                        if etype == "token":
                            yield f"data: {_json_dumps({'token': val})}\n\n"
                            streamed_tokens = True
                        else:
                            yield f"data: {_json_dumps({'stage': val})}\n\n"

                    if task.exception():
                        raise task.exception()
                    result = task.result()
                    full_response = result.response
                    sources = result.sources
                    route_payload["agent_observation_id"] = result.observation_id
                    # Tokens were already streamed token-by-token; only send bulk
                    # response as fallback when the writer stream produced nothing.
                    if not streamed_tokens:
                        yield f"data: {_json_dumps({'token': full_response})}\n\n"
                else:
                    # Emit routing-decision stage immediately
                    from agents.router_agent import _AGENT_STAGE_LABELS
                    routing_label = _AGENT_STAGE_LABELS.get(route.agent_name)
                    if routing_label:
                        yield f"data: {_json_dumps({'stage': routing_label})}\n\n"

                    # Buffer sub-agent stage events and flush before each token
                    _stage_buf: deque[str] = deque()

                    def _push_stage_buf(msg: str) -> None:
                        _stage_buf.append(msg)

                    async for token, is_done, src in route_agent_stream(
                        req.message,
                        conv.thread_id,
                        req.document_ids,
                        route=route,
                        trace_id=trace_id,
                        observation_id=observation_id,
                        on_stage=_push_stage_buf,
                        use_mini=use_mini,
                    ):
                        while _stage_buf:
                            yield f"data: {_json_dumps({'stage': _stage_buf.popleft()})}\n\n"
                        if is_done == "interrupt":
                            yield f"data: {_json_dumps(_interrupt_event(token))}\n\n"
                            return
                        elif is_done:
                            sources = src
                        else:
                            full_response += token
                            yield f"data: {_json_dumps({'token': token})}\n\n"
                    # Flush any remaining stages (e.g. from non-streaming agents)
                    while _stage_buf:
                        yield f"data: {_json_dumps({'stage': _stage_buf.popleft()})}\n\n"
                    route_payload["agent_observation_id"] = observation_id

                    # ── Stream handoff: retrieval → research ─────────────────
            except Exception as e:
                yield f"data: {_json_dumps({'error': str(e)})}\n\n"
                # Commit to persist any conversation state changes (e.g. model field)
                # even when the agent call itself fails.
                try:
                    await asyncio.to_thread(db.commit)
                except Exception:
                    pass
                return

            conv.message_count = (conv.message_count or 0) + 2
            conv.last_agent_name = route_payload.get("agent_name") or route.agent_name

            # Generate title for new conversations using the message pair just exchanged.
            title = conv.title
            if is_new and full_response:
                try:
                    title = await generate_title([
                        {"role": "user", "content": req.message},
                        {"role": "assistant", "content": full_response[:600]},
                    ])
                    conv.title = title
                except Exception:
                    pass

            await asyncio.to_thread(db.commit)
            yield f"data: {_json_dumps({'done': True, 'sources': sources, 'title': title, **route_payload})}\n\n"
        finally:
            def _clear_stream_lock():
                try:
                    conv.stream_started_at = None
                    db.commit()
                except Exception:
                    pass
            await asyncio.to_thread(_clear_stream_lock)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.get("/api/conversations")
def list_conversations(db: Session = Depends(get_db),
                       current_user: User = Depends(get_current_user)):
    convs = (db.query(Conversation)
             .filter(Conversation.user_id == str(current_user.id))
             .order_by(Conversation.created_at.desc()).limit(20).all())
    return [
        {
            "id": c.id,
            "thread_id": c.thread_id,
            "created_at": c.created_at,
            "message_count": c.message_count or 0,
            "model": c.model or "openai",
            "title": c.title,
        }
        for c in convs
    ]


@router.post("/api/conversations/{thread_id}/title")
async def create_conversation_title(thread_id: str, db: Session = Depends(get_db),
                                       current_user: User = Depends(get_current_user)):
    conv = db.query(Conversation).filter(
        Conversation.thread_id == thread_id,
        Conversation.user_id == str(current_user.id),
    ).first()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if conv.title:
        return {"title": conv.title}
    msgs = (await get_thread_messages(thread_id))[:4]
    if not msgs:
        return {"title": None}
    try:
        title = await generate_title(msgs)
        conv.title = title
        db.commit()
        return {"title": title}
    except Exception as e:
        logger.error("Title generation failed", exc_info=True)
        raise HTTPException(status_code=500, detail="Title generation failed")


@router.patch("/api/conversations/{thread_id}/title")
def update_conversation_title(thread_id: str, body: dict, db: Session = Depends(get_db),
                              current_user: User = Depends(get_current_user)):
    conv = db.query(Conversation).filter(
        Conversation.thread_id == thread_id,
        Conversation.user_id == str(current_user.id),
    ).first()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    title = (body.get("title") or "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="標題不可為空")
    conv.title = title
    db.commit()
    return {"title": conv.title}


# ── Human-in-the-loop endpoints (reserved interface) ─────────────────────────
# Interrupt nodes are not yet wired into the agent graph. These endpoints define
# the protocol that the frontend should eventually integrate against. Activate by:
#   1. Adding interrupt() calls inside runner.py agent nodes at decision points
#   2. Removing the "not yet activated" response below

@router.get("/api/conversations/{thread_id}/interrupt")
async def get_conversation_interrupt(thread_id: str, db: Session = Depends(get_db),
                                       current_user: User = Depends(get_current_user)):
    """Check whether the conversation agent is paused at a human-in-the-loop interrupt."""
    conv = db.query(Conversation).filter(
        Conversation.thread_id == thread_id,
        Conversation.user_id == str(current_user.id),
    ).first()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    pending = await get_pending_interrupt(thread_id)
    return {
        "has_interrupt": pending is not None,
        "interrupt_id": pending["interrupt_id"] if pending else None,
        "value": _json_safe(pending["value"]) if pending else None,
    }


class ResumeRequest(BaseModel):
    decisions: list[dict]
    interrupt_id: Optional[str] = None


@router.post("/api/conversations/{thread_id}/resume")
async def resume_conversation(thread_id: str, body: ResumeRequest, db: Session = Depends(get_db),
                              current_user: User = Depends(get_current_user)):
    """Resume an agent that is paused at a human-in-the-loop interrupt."""
    conv = db.query(Conversation).filter(
        Conversation.thread_id == thread_id,
        Conversation.user_id == str(current_user.id),
    ).first()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    async def resume_stream():
        saw_event = False
        async for token, is_done, sources in run_tool_agent_resume_stream(
            thread_id,
            body.decisions,
            interrupt_id=body.interrupt_id,
        ):
            saw_event = True
            if is_done == "interrupt":
                yield f"data: {_json_dumps(_interrupt_event(token))}\n\n"
                return
            if is_done:
                yield f"data: {_json_dumps({'done': True, 'sources': sources})}\n\n"
            else:
                yield f"data: {_json_dumps({'token': token})}\n\n"
        if not saw_event:
            yield f"data: {_json_dumps({'error': 'No pending interrupt for this conversation'})}\n\n"

    return StreamingResponse(resume_stream(), media_type="text/event-stream")


@router.delete("/api/conversations/{thread_id}")
def delete_conversation(thread_id: str, db: Session = Depends(get_db),
                        current_user: User = Depends(get_current_user)):
    conv = db.query(Conversation).filter(
        Conversation.thread_id == thread_id,
        Conversation.user_id == str(current_user.id),
    ).first()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    db.delete(conv)
    db.commit()
    return {"ok": True}


@router.get("/api/conversations/{thread_id}/messages")
async def get_messages(thread_id: str, db: Session = Depends(get_db),
                       current_user: User = Depends(get_current_user)):
    conv = db.query(Conversation).filter(
        Conversation.thread_id == thread_id,
        Conversation.user_id == str(current_user.id),
    ).first()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    messages = await get_thread_messages(thread_id)
    messages = _hydrate_message_attachments(thread_id, messages, db)
    return _hydrate_assistant_meta(thread_id, messages, db)
