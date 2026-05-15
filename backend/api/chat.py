import asyncio
import json
import logging
import uuid
from typing import Optional

logger = logging.getLogger(__name__)

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from agents.runner import generate_title, get_thread_messages, get_pending_interrupt, resume_from_interrupt
from agents.router_agent import (
    run_chat_agent,
    run_research_agent,
    classify_intent,
    route_agent_message,
    route_agent_stream,
)
from agents.request_context import set_user_id
from db import get_db, Conversation, Document, Trace

router = APIRouter()


class ChatRequest(BaseModel):
    message: str
    conversation_id: Optional[int] = None
    model: str = "gemini"
    document_ids: Optional[list[int]] = None
    user_id: Optional[str] = None


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


def _hydrate_message_attachments(conv_id: int, messages: list[dict], db: Session) -> list[dict]:
    """Attach document chips to user messages using root trace document_ids."""
    traces = (
        db.query(Trace.document_ids)
        .filter(
            Trace.agent_name == "router_agent",
            Trace.thread_id == str(conv_id),
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


def _hydrate_assistant_meta(conv_id: int, messages: list[dict], db: Session) -> list[dict]:
    """Attach route/trace metadata to assistant messages using router-level traces by turn."""
    traces = (
        db.query(
            Trace.run_id,
            Trace.task_type,
            Trace.route_intent,
            Trace.agent_name,
            Trace.prompt_name,
            Trace.prompt_version,
        )
        .filter(Trace.agent_name == "router_agent", Trace.thread_id == str(conv_id))
        .order_by(Trace.start_time.asc())
        .all()
    )

    hydrated = []
    assistant_turn = 0
    for msg in messages:
        item = dict(msg)
        if item.get("role") == "assistant":
            if assistant_turn < len(traces):
                run_id, task_type, route_intent, agent_name, prompt_name, prompt_version = traces[assistant_turn]
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
                if run_id:
                    item["trace_run_id"] = run_id
            assistant_turn += 1
        hydrated.append(item)
    return hydrated


@router.post("/api/chat")
async def chat(req: ChatRequest, db: Session = Depends(get_db)):
    if req.conversation_id:
        conv = db.query(Conversation).filter(Conversation.id == req.conversation_id).first()
        if not conv:
            raise HTTPException(status_code=404, detail="Conversation not found")
        conv.model = req.model
    else:
        conv = Conversation(model=req.model)
        db.add(conv)
        db.commit()
        db.refresh(conv)

    set_user_id(req.user_id)
    try:
        prev_intent_sync: str | None = None
        if req.conversation_id:
            last = (
                db.query(Trace.route_intent)
                .filter(Trace.agent_name == "router_agent", Trace.thread_id == str(conv.id))
                .order_by(Trace.start_time.desc())
                .first()
            )
            prev_intent_sync = last[0] if last else None
        route = await classify_intent(req.message, req.document_ids, str(conv.id), previous_intent=prev_intent_sync)
        router_run_id = str(uuid.uuid4())
        use_mini = _should_use_mini(req.model, route.intent, req.document_ids)
        result = await route_agent_message(
            req.message,
            str(conv.id),
            req.document_ids,
            route=route,
            run_id=router_run_id,
            _hop_count=0,
            use_mini=use_mini,
        )
        response, sources = result.response, result.sources
    except Exception as e:
        logger.error("Agent error in chat endpoint", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal agent error")

    conv.message_count = (conv.message_count or 0) + 2
    db.commit()
    return {
        "conversation_id": conv.id,
        "response": response,
        "sources": sources,
        "task_type": result.task_type,
        "route_intent": result.route_intent,
        "agent_name": result.agent_name,
        "prompt_name": result.prompt_name,
        "prompt_version": result.prompt_version,
        "original_intent": route.original_intent,
        "resolved_intent": route.resolved_intent,
        "trace_run_id": router_run_id,
        "agent_trace_run_id": result.trace_run_id,
    }


@router.post("/api/chat/stream")
async def chat_stream(req: ChatRequest, db: Session = Depends(get_db)):
    if req.conversation_id:
        conv = db.query(Conversation).filter(Conversation.id == req.conversation_id).first()
        if not conv:
            raise HTTPException(status_code=404, detail="Conversation not found")
        conv.model = req.model
    else:
        conv = Conversation(model=req.model)
        db.add(conv)
        db.commit()
        db.refresh(conv)

    conv_id = conv.id
    is_new = req.conversation_id is None  # title only generated for brand-new conversations

    set_user_id(req.user_id)
    # Carry the last turn's intent so follow-up questions stay in the same route.
    prev_intent: str | None = None
    if req.conversation_id:
        last_trace = (
            db.query(Trace.route_intent)
            .filter(Trace.agent_name == "router_agent", Trace.thread_id == str(conv_id))
            .order_by(Trace.start_time.desc())
            .first()
        )
        prev_intent = last_trace[0] if last_trace else None

    route = await classify_intent(req.message, req.document_ids, str(conv_id), previous_intent=prev_intent)
    router_run_id = str(uuid.uuid4())
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
            "trace_run_id": router_run_id,
            "agent_trace_run_id": None,
        }
        yield f"data: {json.dumps({'conversation_id': conv_id, **route_payload})}\n\n"
        task_run_id = str(uuid.uuid4())
        try:
            if route.intent == "research":
                # Research streams writer tokens in real time via on_token;
                # stage progress events continue to flow via on_stage.
                stage_queue: asyncio.Queue[str] = asyncio.Queue()
                token_queue: asyncio.Queue[str] = asyncio.Queue()

                def _push_stage(msg: str) -> None:
                    try:
                        stage_queue.put_nowait(msg)
                    except Exception:
                        pass

                def _push_token(t: str) -> None:
                    try:
                        token_queue.put_nowait(t)
                    except Exception:
                        pass

                task = asyncio.create_task(
                    run_research_agent(
                        req.message, str(conv_id), req.document_ids,
                        run_id=task_run_id,
                        parent_run_id=router_run_id,
                        on_stage=_push_stage,
                        on_token=_push_token,
                        original_intent=route.original_intent,
                        resolved_intent=route.resolved_intent,
                    )
                )
                streamed_tokens = False
                while not task.done():
                    await asyncio.sleep(0.05)
                    while not token_queue.empty():
                        yield f"data: {json.dumps({'token': token_queue.get_nowait()})}\n\n"
                        streamed_tokens = True
                    while not stage_queue.empty():
                        yield f"data: {json.dumps({'stage': stage_queue.get_nowait()})}\n\n"

                # Drain any remaining events
                while not token_queue.empty():
                    yield f"data: {json.dumps({'token': token_queue.get_nowait()})}\n\n"
                    streamed_tokens = True
                while not stage_queue.empty():
                    yield f"data: {json.dumps({'stage': stage_queue.get_nowait()})}\n\n"

                if task.exception():
                    raise task.exception()
                result = task.result()
                full_response = result.response
                sources = result.sources
                route_payload["agent_trace_run_id"] = result.trace_run_id
                # Tokens were already streamed token-by-token; only send bulk
                # response as fallback when the writer stream produced nothing.
                if not streamed_tokens:
                    yield f"data: {json.dumps({'token': full_response})}\n\n"
            elif route.intent == "chat":
                stage_queue_chat: asyncio.Queue[str] = asyncio.Queue()

                def _push_chat(msg: str) -> None:
                    try:
                        stage_queue_chat.put_nowait(msg)
                    except Exception:
                        pass

                chat_task = asyncio.create_task(
                    run_chat_agent(
                        req.message, str(conv_id), req.document_ids,
                        run_id=task_run_id,
                        parent_run_id=router_run_id,
                        on_stage=_push_chat,
                        use_mini=use_mini,
                        original_intent=route.original_intent,
                        resolved_intent=route.resolved_intent,
                    )
                )
                while not chat_task.done():
                    await asyncio.sleep(0.25)
                    while not stage_queue_chat.empty():
                        yield f"data: {json.dumps({'stage': stage_queue_chat.get_nowait()})}\n\n"
                while not stage_queue_chat.empty():
                    yield f"data: {json.dumps({'stage': stage_queue_chat.get_nowait()})}\n\n"
                if chat_task.exception():
                    raise chat_task.exception()
                chat_result = chat_task.result()
                full_response = chat_result.response
                sources = chat_result.sources
                route_payload["agent_trace_run_id"] = chat_result.trace_run_id
                yield f"data: {json.dumps({'token': full_response})}\n\n"
            else:
                async for token, is_done, src in route_agent_stream(
                    req.message,
                    str(conv_id),
                    req.document_ids,
                    route=route,
                    run_id=router_run_id,
                    task_run_id=task_run_id,
                    use_mini=use_mini,
                ):
                    if is_done:
                        sources = src
                    else:
                        full_response += token
                        yield f"data: {json.dumps({'token': token})}\n\n"
                route_payload["agent_trace_run_id"] = task_run_id

                # ── Stream handoff: retrieval → research ─────────────────
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
            # Commit to persist any conversation state changes (e.g. model field)
            # even when the agent call itself fails.
            try:
                db.commit()
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

        db.commit()
        yield f"data: {json.dumps({'done': True, 'sources': sources, 'title': title, **route_payload})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.get("/api/conversations")
def list_conversations(db: Session = Depends(get_db)):
    convs = db.query(Conversation).order_by(Conversation.created_at.desc()).limit(20).all()
    return [
        {
            "id": c.id,
            "created_at": c.created_at,
            "message_count": c.message_count or 0,
            "model": c.model or "openai",
            "title": c.title,
        }
        for c in convs
    ]


@router.post("/api/conversations/{conv_id}/title")
async def create_conversation_title(conv_id: int, db: Session = Depends(get_db)):
    conv = db.query(Conversation).filter(Conversation.id == conv_id).first()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if conv.title:
        return {"title": conv.title}
    msgs = (await get_thread_messages(str(conv_id)))[:4]
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


@router.patch("/api/conversations/{conv_id}/title")
def update_conversation_title(conv_id: int, body: dict, db: Session = Depends(get_db)):
    conv = db.query(Conversation).filter(Conversation.id == conv_id).first()
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

@router.get("/api/conversations/{conv_id}/interrupt")
async def get_conversation_interrupt(conv_id: int, db: Session = Depends(get_db)):
    """Check whether the conversation agent is paused at a human-in-the-loop interrupt.

    Returns:
        has_interrupt: True if the agent is waiting for user input.
        interrupt_id: Opaque identifier needed to resume.
        value: The interrupt payload (e.g. proposed action, question for user).
    """
    conv = db.query(Conversation).filter(Conversation.id == conv_id).first()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    pending = await get_pending_interrupt(str(conv_id))
    return {
        "has_interrupt": pending is not None,
        "interrupt_id": pending["interrupt_id"] if pending else None,
        "value": pending["value"] if pending else None,
    }


class ResumeRequest(BaseModel):
    response: str
    interrupt_id: Optional[str] = None


@router.post("/api/conversations/{conv_id}/resume")
async def resume_conversation(conv_id: int, body: ResumeRequest, db: Session = Depends(get_db)):
    """Resume an agent that is paused at a human-in-the-loop interrupt.

    The frontend sends the user's decision/feedback as `response`.
    The agent continues from the interrupt point with that value.
    """
    conv = db.query(Conversation).filter(Conversation.id == conv_id).first()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    ok = await resume_from_interrupt(str(conv_id), body.response)
    if not ok:
        raise HTTPException(status_code=409, detail="No pending interrupt for this conversation")
    return {"resumed": True}


@router.delete("/api/conversations/{conv_id}")
def delete_conversation(conv_id: int, db: Session = Depends(get_db)):
    conv = db.query(Conversation).filter(Conversation.id == conv_id).first()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    db.delete(conv)
    db.commit()
    return {"ok": True}


@router.get("/api/conversations/{conv_id}/messages")
async def get_messages(conv_id: int, db: Session = Depends(get_db)):
    conv = db.query(Conversation).filter(Conversation.id == conv_id).first()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    messages = await get_thread_messages(str(conv_id))
    messages = _hydrate_message_attachments(conv_id, messages, db)
    return _hydrate_assistant_meta(conv_id, messages, db)
