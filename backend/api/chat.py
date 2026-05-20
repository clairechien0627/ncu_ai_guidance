import asyncio
import json
import logging
import uuid
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
    classify_intent,
    route_agent_message,
    route_agent_stream,
)
from agents.request_context import set_user_id
from db import get_db, Conversation, Document, Trace

router = APIRouter()


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
    user_id: Optional[str] = None
    bypass_cache: bool = False


def _should_use_mini(model: str, intent: str, document_ids: list[int] | None) -> bool:
    """Use the mini model when explicitly requested OR for simple chat without documents."""
    if "mini" in (model or "").lower():
        return True
    # Pure conversation (no documents, chat intent) → mini is sufficient
    if intent == "chat" and not document_ids:
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
            Trace.route_intent,
            Trace.agent_name,
            Trace.prompt_name,
            Trace.prompt_version,
        )
        .filter(Trace.agent_name == "router_agent", Trace.thread_id == thread_id)
        .order_by(Trace.start_time.asc())
        .all()
    )

    hydrated = []
    assistant_turn = 0
    for msg in messages:
        item = dict(msg)
        if item.get("role") == "assistant":
            if assistant_turn < len(traces):
                observation_id, task_type, route_intent, agent_name, prompt_name, prompt_version = traces[assistant_turn]
                if task_type:
                    item["task_type"] = task_type
                if route_intent:
                    item["route_intent"] = route_intent
                if agent_name:
                    item["agent_name"] = agent_name
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
async def chat(req: ChatRequest, db: Session = Depends(get_db)):
    def _setup_conv():
        if req.thread_id:
            c = db.query(Conversation).filter(Conversation.thread_id == req.thread_id).first()
            if not c:
                raise HTTPException(status_code=404, detail="Conversation not found")
            c.model = req.model
            return c
        c = Conversation(model=req.model, thread_id=new_id())
        db.add(c)
        db.commit()
        db.refresh(c)
        return c

    conv = await asyncio.to_thread(_setup_conv)

    if req.user_id:
        set_user_id(req.user_id)
    try:
        def _get_prev_intent():
            if not req.thread_id:
                return None
            last = (
                db.query(Trace.route_intent)
                .filter(Trace.agent_name == "router_agent", Trace.thread_id == conv.thread_id)
                .order_by(Trace.start_time.desc())
                .first()
            )
            return last[0] if last else None

        prev_intent_sync = await asyncio.to_thread(_get_prev_intent)
        route = await classify_intent(req.message, req.document_ids, conv.thread_id, previous_intent=prev_intent_sync)
        trace_id = new_id()
        use_mini = _should_use_mini(req.model, route.intent, req.document_ids)
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

    await asyncio.to_thread(lambda: (setattr(conv, "message_count", (conv.message_count or 0) + 2), db.commit()))
    return {
        "thread_id": conv.thread_id,
        "response": response,
        "sources": sources,
        "task_type": result.task_type,
        "route_intent": result.route_intent,
        "agent_name": result.agent_name,
        "prompt_name": result.prompt_name,
        "prompt_version": result.prompt_version,
        "original_intent": route.original_intent,
        "resolved_intent": route.resolved_intent,
        "trace_id": trace_id,
        "agent_observation_id": result.observation_id,
    }


@router.post("/api/chat/stream")
async def chat_stream(req: ChatRequest, db: Session = Depends(get_db)):
    def _setup_conv():
        if req.thread_id:
            c = db.query(Conversation).filter(Conversation.thread_id == req.thread_id).first()
            if not c:
                raise HTTPException(status_code=404, detail="Conversation not found")
            c.model = req.model
            return c
        c = Conversation(model=req.model, thread_id=new_id())
        db.add(c)
        db.commit()
        db.refresh(c)
        return c

    conv = await asyncio.to_thread(_setup_conv)
    is_new = req.thread_id is None

    if req.user_id:
        set_user_id(req.user_id)
    def _get_prev_intent():
        if not req.thread_id:
            return None
        last_trace = (
            db.query(Trace.route_intent)
            .filter(Trace.agent_name == "router_agent", Trace.thread_id == conv.thread_id)
            .order_by(Trace.start_time.desc())
            .first()
        )
        return last_trace[0] if last_trace else None

    prev_intent = await asyncio.to_thread(_get_prev_intent)

    route = await classify_intent(req.message, req.document_ids, conv.thread_id, previous_intent=prev_intent)
    trace_id = new_id()
    use_mini = _should_use_mini(req.model, route.intent, req.document_ids)

    async def event_stream():
        sources = []
        full_response = ""
        route_payload = {
            "task_type": "chat_turn",
            "route_intent": route.intent,
            "agent_name": route.agent_name,
            "prompt_name": route.prompt_name,
            "prompt_version": route.prompt_version,
            "original_intent": route.original_intent,
            "resolved_intent": route.resolved_intent,
            "trace_id": trace_id,
            "agent_observation_id": None,
        }
        yield f"data: {_json_dumps({'thread_id': conv.thread_id, **route_payload})}\n\n"
        observation_id = new_id()
        try:
            if route.intent == "research":
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
                        original_intent=route.original_intent,
                        resolved_intent=route.resolved_intent,
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
                async for token, is_done, src in route_agent_stream(
                    req.message,
                    conv.thread_id,
                    req.document_ids,
                    route=route,
                    trace_id=trace_id,
                    observation_id=observation_id,
                    use_mini=use_mini,
                ):
                    if is_done == "interrupt":
                        yield f"data: {_json_dumps(_interrupt_event(token))}\n\n"
                        return
                    elif is_done:
                        sources = src
                    else:
                        full_response += token
                        yield f"data: {_json_dumps({'token': token})}\n\n"
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

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.get("/api/conversations")
def list_conversations(db: Session = Depends(get_db)):
    convs = db.query(Conversation).order_by(Conversation.created_at.desc()).limit(20).all()
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
async def create_conversation_title(thread_id: str, db: Session = Depends(get_db)):
    conv = db.query(Conversation).filter(Conversation.thread_id == thread_id).first()
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
def update_conversation_title(thread_id: str, body: dict, db: Session = Depends(get_db)):
    conv = db.query(Conversation).filter(Conversation.thread_id == thread_id).first()
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
async def get_conversation_interrupt(thread_id: str, db: Session = Depends(get_db)):
    """Check whether the conversation agent is paused at a human-in-the-loop interrupt."""
    conv = db.query(Conversation).filter(Conversation.thread_id == thread_id).first()
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
async def resume_conversation(thread_id: str, body: ResumeRequest, db: Session = Depends(get_db)):
    """Resume an agent that is paused at a human-in-the-loop interrupt."""
    conv = db.query(Conversation).filter(Conversation.thread_id == thread_id).first()
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
def delete_conversation(thread_id: str, db: Session = Depends(get_db)):
    conv = db.query(Conversation).filter(Conversation.thread_id == thread_id).first()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    db.delete(conv)
    db.commit()
    return {"ok": True}


@router.get("/api/conversations/{thread_id}/messages")
async def get_messages(thread_id: str, db: Session = Depends(get_db)):
    conv = db.query(Conversation).filter(Conversation.thread_id == thread_id).first()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    messages = await get_thread_messages(thread_id)
    messages = _hydrate_message_attachments(thread_id, messages, db)
    return _hydrate_assistant_meta(thread_id, messages, db)
