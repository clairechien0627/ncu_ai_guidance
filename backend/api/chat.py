import asyncio
import json
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from agents.runner import generate_title, get_thread_messages
from agents.main_agent import (
    MAX_HANDOFFS,
    _run_chat_agent,
    _run_research_agent,
    classify_intent,
    route_agent_message,
    route_agent_stream,
    _route_for_intent,
)
from database import get_db, Conversation, Document, Trace

router = APIRouter()


class ChatRequest(BaseModel):
    message: str
    conversation_id: Optional[int] = None
    model: str = "gemini"
    document_ids: Optional[list[int]] = None


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
            Trace.parent_run_id.is_(None),
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
    """Attach route/trace metadata to assistant messages using root traces by turn."""
    traces = (
        db.query(
            Trace.run_id,
            Trace.mode,
            Trace.agent_name,
            Trace.prompt_name,
            Trace.prompt_version,
        )
        .filter(Trace.parent_run_id.is_(None), Trace.thread_id == str(conv_id))
        .order_by(Trace.start_time.asc())
        .all()
    )

    hydrated = []
    assistant_turn = 0
    for msg in messages:
        item = dict(msg)
        if item.get("role") == "assistant":
            if assistant_turn < len(traces):
                run_id, mode, agent_name, prompt_name, prompt_version = traces[assistant_turn]
                if mode:
                    item["mode"] = mode
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

    try:
        prev_intent_sync: str | None = None
        if req.conversation_id:
            last = (
                db.query(Trace.mode)
                .filter(Trace.parent_run_id.is_(None), Trace.thread_id == str(conv.id))
                .order_by(Trace.start_time.desc())
                .first()
            )
            prev_intent_sync = last[0] if last else None
        route = await classify_intent(req.message, req.document_ids, str(conv.id), previous_intent=prev_intent_sync)
        run_id = str(uuid.uuid4())
        use_mini = _should_use_mini(req.model, route.intent, req.document_ids)
        result = await route_agent_message(
            req.message,
            str(conv.id),
            req.document_ids,
            route=route,
            run_id=run_id,
            _hop_count=0,
            use_mini=use_mini,
        )
        # Agent may request a handoff to another intent (swarm-style delegation).
        hop = 1
        while result.next_intent and hop < MAX_HANDOFFS:
            new_route = _route_for_intent(result.next_intent, req.document_ids, str(conv.id))
            result = await route_agent_message(
                req.message,
                str(conv.id),
                req.document_ids,
                route=new_route,
                run_id=run_id,
                _hop_count=hop,
                use_mini=use_mini,
            )
            hop += 1
        response, sources = result.response, result.sources
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Agent error: {e}")

    conv.message_count = (conv.message_count or 0) + 2
    db.commit()
    return {
        "conversation_id": conv.id,
        "response": response,
        "sources": sources,
        "mode": result.mode,
        "agent_name": result.agent_name,
        "prompt_name": result.prompt_name,
        "prompt_version": result.prompt_version,
        "original_intent": route.original_intent,
        "resolved_intent": route.resolved_intent,
        "trace_run_id": result.trace_run_id,
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

    # Carry the last turn's intent so follow-up questions stay in the same mode
    prev_intent: str | None = None
    if req.conversation_id:
        last_trace = (
            db.query(Trace.mode)
            .filter(Trace.parent_run_id.is_(None), Trace.thread_id == str(conv_id))
            .order_by(Trace.start_time.desc())
            .first()
        )
        prev_intent = last_trace[0] if last_trace else None

    route = await classify_intent(req.message, req.document_ids, str(conv_id), previous_intent=prev_intent)
    run_id = str(uuid.uuid4())
    use_mini = _should_use_mini(req.model, route.intent, req.document_ids)

    async def event_stream():
        sources = []
        full_response = ""
        route_payload = {
            "mode": route.intent,
            "agent_name": route.agent_name,
            "prompt_name": route.prompt_name,
            "prompt_version": route.prompt_version,
            "original_intent": route.original_intent,
            "resolved_intent": route.resolved_intent,
            "trace_run_id": run_id,
        }
        yield f"data: {json.dumps({'conversation_id': conv_id, **route_payload})}\n\n"
        try:
            if route.intent == "research":
                # Research is non-streaming internally; run as asyncio task so we can
                # emit stage events while it works.
                stage_queue: asyncio.Queue[str] = asyncio.Queue()

                def _push_stage(msg: str) -> None:
                    try:
                        stage_queue.put_nowait(msg)
                    except Exception:
                        pass

                task = asyncio.create_task(
                    _run_research_agent(
                        req.message, str(conv_id), req.document_ids,
                        run_id=run_id,
                        on_stage=_push_stage,
                        original_intent=route.original_intent,
                        resolved_intent=route.resolved_intent,
                    )
                )
                while not task.done():
                    await asyncio.sleep(0.25)
                    while not stage_queue.empty():
                        yield f"data: {json.dumps({'stage': stage_queue.get_nowait()})}\n\n"

                # Drain any remaining stage messages
                while not stage_queue.empty():
                    yield f"data: {json.dumps({'stage': stage_queue.get_nowait()})}\n\n"

                if task.exception():
                    raise task.exception()
                result = task.result()
                full_response = result.response
                sources = result.sources
                yield f"data: {json.dumps({'token': full_response})}\n\n"
            elif route.intent == "chat":
                # Chat uses the orchestrator which may internally call research.
                # Run as a Task so stage events from call_research_agent propagate.
                stage_queue_chat: asyncio.Queue[str] = asyncio.Queue()

                def _push_chat(msg: str) -> None:
                    try:
                        stage_queue_chat.put_nowait(msg)
                    except Exception:
                        pass

                chat_task = asyncio.create_task(
                    _run_chat_agent(
                        req.message, str(conv_id), req.document_ids,
                        run_id=run_id, on_stage=_push_chat,
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
                yield f"data: {json.dumps({'token': full_response})}\n\n"
            else:
                async for token, is_done, src in route_agent_stream(
                    req.message,
                    str(conv_id),
                    req.document_ids,
                    route=route,
                    run_id=run_id,
                    use_mini=use_mini,
                ):
                    if is_done:
                        sources = src
                    else:
                        full_response += token
                        yield f"data: {json.dumps({'token': token})}\n\n"

                # ── Stream handoff: retrieval → research ─────────────────
                # When retrieval finds nothing AND the question asked for
                # comprehensive coverage, escalate to the full research pipeline.
                _research_keywords = (
                    "摘要", "總結", "重點整理", "懶人包",
                    "研究動機", "研究方法", "研究成果", "研究限制",
                    "summary", "summarize", "overview",
                )
                if (
                    route.intent == "retrieval"
                    and req.document_ids
                    and not sources
                    and any(kw in req.message.lower() for kw in _research_keywords)
                ):
                    yield f"data: {json.dumps({'stage': '切換深度研究模式'})}\n\n"
                    stage_queue2: asyncio.Queue[str] = asyncio.Queue()

                    def _push2(msg: str) -> None:
                        try:
                            stage_queue2.put_nowait(msg)
                        except Exception:
                            pass

                    research_task = asyncio.create_task(
                        _run_research_agent(
                            req.message, str(conv_id), req.document_ids,
                            run_id=run_id,
                            on_stage=_push2,
                            original_intent="retrieval_handoff",
                            resolved_intent="research",
                        )
                    )
                    while not research_task.done():
                        await asyncio.sleep(0.25)
                        while not stage_queue2.empty():
                            yield f"data: {json.dumps({'stage': stage_queue2.get_nowait()})}\n\n"
                    while not stage_queue2.empty():
                        yield f"data: {json.dumps({'stage': stage_queue2.get_nowait()})}\n\n"
                    if not research_task.exception():
                        res2 = research_task.result()
                        full_response = res2.response
                        sources = res2.sources
                        yield f"data: {json.dumps({'token': full_response})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
            return

        conv.message_count = (conv.message_count or 0) + 2

        # Generate title for new conversations using the message pair just exchanged.
        title = conv.title
        if is_new and full_response:
            try:
                title = await asyncio.to_thread(generate_title, [
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
        title = await asyncio.to_thread(generate_title, msgs)
        conv.title = title
        db.commit()
        return {"title": title}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


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
