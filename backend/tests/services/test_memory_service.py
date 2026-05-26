import asyncio
import json
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agents.types import AgentResult
from db.models import Conversation
from db.session import Base
from services.memory_service import get_context_summary_text, update_context_summary


def _session_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)


def test_context_summary_loads_by_uuid_thread_id(monkeypatch):
    SessionLocal = _session_factory()
    monkeypatch.setattr("db.session.SessionLocal", SessionLocal)

    db = SessionLocal()
    try:
        db.add(Conversation(
            thread_id="thread-uuid-1",
            user_id="public-user-1",
            model="gemini",
            context_summary=json.dumps({
                "version": 2,
                "user_focus": "follow-up focus",
                "findings": [{
                    "question": "old research question",
                    "coverage": {
                        "method": {
                            "status": "FILLED",
                            "label": "方法",
                            "notes": ["old method note"],
                        },
                    },
                    "sources": ["paper.pdf p.1"],
                }],
            }, ensure_ascii=False),
            created_at=datetime.utcnow(),
        ))
        db.commit()
    finally:
        db.close()

    text = get_context_summary_text("thread-uuid-1")

    assert text is not None
    assert "old research question" in text
    assert "old method note" in text


def test_update_context_summary_preserves_existing_findings_for_uuid_thread_id(monkeypatch):
    SessionLocal = _session_factory()
    monkeypatch.setattr("db.session.SessionLocal", SessionLocal)

    db = SessionLocal()
    try:
        db.add(Conversation(
            thread_id="thread-uuid-1",
            user_id="public-user-1",
            model="gemini",
            context_summary=json.dumps({
                "version": 2,
                "user_focus": "old focus",
                "findings": [{
                    "question": "old research question",
                    "coverage": {
                        "method": {
                            "status": "FILLED",
                            "label": "方法",
                            "notes": ["old method note"],
                        },
                    },
                    "sources": ["paper.pdf p.1"],
                }],
            }, ensure_ascii=False),
            created_at=datetime.utcnow(),
        ))
        db.commit()
    finally:
        db.close()

    result = AgentResult(
        response="new answer",
        sources=["paper.pdf p.2"],
        agent_name="research",
        coverage_result={
            "results": {
                "status": "PARTIAL",
                "label": "成果",
                "notes": ["new result note"],
            },
        },
    )

    asyncio.run(update_context_summary("thread-uuid-1", "new research question", result))

    db = SessionLocal()
    try:
        conv = db.query(Conversation).filter(Conversation.thread_id == "thread-uuid-1").one()
        summary = json.loads(conv.context_summary)
    finally:
        db.close()

    questions = [finding["question"] for finding in summary["findings"]]
    assert "old research question" in questions
    assert "new research question" in questions
