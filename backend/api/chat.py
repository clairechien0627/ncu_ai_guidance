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
    route_request,
    route_agent_message,
    route_agent_stream,
)
import agents.steering as steering
import agents.chat_jobs as chat_jobs
from agents.request_context import set_user_id
from api.dependencies import get_current_user
from db import get_db, Conversation, Document
from db.models import TraceV2, User
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
    if agent_name == "chat" and not document_ids:
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
    attachments_by_turn = []
    for row in (
        db.query(TraceV2)
        .filter(TraceV2.name == "router_agent", TraceV2.thread_id == thread_id)
        .order_by(TraceV2.start_time.asc())
        .all()
    ):
        meta = (json.loads(row.metadata_json) if isinstance(row.metadata_json, str) else row.metadata_json) or {}
        if not isinstance(meta, dict):
            continue
        doc_ids = meta.get("document_ids")
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
    """Attach request trace id and routed agent name to assistant messages by turn."""
    traces = (
        db.query(TraceV2.trace_id, TraceV2.metadata_json)
        .filter(TraceV2.name == "router_agent", TraceV2.thread_id == thread_id)
        .order_by(TraceV2.start_time.asc())
        .all()
    )

    hydrated = []
    assistant_turn = 0
    for msg in messages:
        item = dict(msg)
        if item.get("role") == "assistant":
            if assistant_turn < len(traces):
                trace_id, meta_json = traces[assistant_turn]
                meta = (json.loads(meta_json) if isinstance(meta_json, str) else meta_json) or {}
                if isinstance(meta, dict) and meta.get("agent_name"):
                    item["agent_name"] = meta["agent_name"]
                if trace_id:
                    item["trace_id"] = trace_id
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
    chat_jobs.start(conv.thread_id, req.message, user_id=str(current_user.id), title=conv.title)
    from services.job_service import push_jobs as _push_jobs
    asyncio.create_task(_push_jobs())

    is_new = req.thread_id is None
    set_user_id(str(current_user.id))

    prev_agent = await asyncio.to_thread(_get_previous_agent, db, conv.thread_id if req.thread_id else None)
    trace_id = new_id()
    use_mini = "mini" in (req.model or "").lower()

    async def event_stream():
        from services.llm_gate import get_gate
        _gate = get_gate()
        await _gate.acquire()
        sources = []
        full_response = ""
        route_payload = {
            "agent_name": None,
            "prompt_name": None,
            "prompt_version": None,
            "trace_id": trace_id,
            "agent_observation_id": None,
        }
        _initial_emitted = False
        _stage_buf: deque[str] = deque()

        def _on_route(r: "AgentRoute") -> None:
            route_payload["agent_name"] = r.agent_name
            route_payload["prompt_name"] = r.prompt_name
            route_payload["prompt_version"] = r.prompt_version

        def _push_stage(msg: str) -> None:
            _stage_buf.append(msg)

        observation_id = new_id()
        try:
            try:
                async for token, is_done, src in route_agent_stream(
                    req.message,
                    conv.thread_id,
                    req.document_ids,
                    trace_id=trace_id,
                    observation_id=observation_id,
                    on_stage=_push_stage,
                    on_route=_on_route,
                    previous_agent_name=prev_agent,
                    bypass_cache=req.bypass_cache,
                    use_mini=use_mini,
                ):
                    if not _initial_emitted:
                        yield f"data: {_json_dumps({'thread_id': conv.thread_id, **route_payload})}\n\n"
                        _initial_emitted = True
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
                while _stage_buf:
                    yield f"data: {_json_dumps({'stage': _stage_buf.popleft()})}\n\n"
                route_payload["agent_observation_id"] = observation_id

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
            conv.last_agent_name = route_payload.get("agent_name")

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
            _gate.release()
            chat_jobs.finish(conv.thread_id)
            asyncio.create_task(_push_jobs())
            def _clear_stream_lock():
                try:
                    conv.stream_started_at = None
                    db.commit()
                except Exception:
                    pass
            await asyncio.to_thread(_clear_stream_lock)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.get("/api/chat/active")
def get_active_chats(current_user: User = Depends(get_current_user)):
    """Return currently streaming chat sessions for this user."""
    return [j for j in chat_jobs.get_active() if j.get("user_id") == str(current_user.id)]


@router.post("/api/chat/{thread_id}/cancel")
async def cancel_chat(thread_id: str, current_user: User = Depends(get_current_user),
                      db: Session = Depends(get_db)):
    conv = db.query(Conversation).filter(
        Conversation.thread_id == thread_id,
        Conversation.user_id == str(current_user.id),
    ).first()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    ok = chat_jobs.request_cancel(thread_id)
    return {"ok": ok, "thread_id": thread_id}


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
