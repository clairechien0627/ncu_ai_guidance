"""Unified memory abstraction for all agents.

Read and write policies are declared here; implementation lives in memory_service.py.
The router uses this module to inject memory context and record outcomes — individual
agents do not call memory_service directly.

Policy rules:
- Only research writes to long-term memory (store) and context_summary.
- Retrieval/question/chat can read context_summary for follow-up disambiguation.
- Long-term memory (semantic search) is enabled for research and chat only.
- doc_cache summaries are available to research and retrieval (document-grounded tasks).
- retrieval memory is framing context only, never citation evidence.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agents.types import AgentResult

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MemoryRead:
    context_summary: bool = True
    long_term: bool = False   # semantic search in LangGraph Store
    doc_cache: bool = False   # document_research_cache slot summaries


@dataclass(frozen=True)
class MemoryWrite:
    context_summary: bool = False
    long_term: bool = False   # write to LangGraph Store


MEMORY_READ_POLICY: dict[str, MemoryRead] = {
    "research":  MemoryRead(context_summary=True,  long_term=True,  doc_cache=True),
    "retrieval": MemoryRead(context_summary=True,  long_term=False, doc_cache=True),
    "question":  MemoryRead(context_summary=True,  long_term=False, doc_cache=False),
    "chat":      MemoryRead(context_summary=True,  long_term=True,  doc_cache=False),
    "summary":   MemoryRead(context_summary=True,  long_term=False, doc_cache=True),
}

MEMORY_WRITE_POLICY: dict[str, MemoryWrite] = {
    "research":  MemoryWrite(context_summary=True,  long_term=True),
    "retrieval": MemoryWrite(context_summary=False, long_term=False),
    "question":  MemoryWrite(context_summary=False, long_term=False),
    "chat":      MemoryWrite(context_summary=False, long_term=False),
    "summary":   MemoryWrite(context_summary=True,  long_term=True),
}

_CONTEXT_LABELS = {
    "context_summary": "【對話摘要】以下是本次對話的研究摘要，作為追問背景，不可作為 citation：\n",
    "long_term":       "【歷史研究記憶】以下是過去相關研究的摘要，僅供參考：\n",
    "doc_cache":       "【文件已知資訊】以下文件曾被研究過，主要發現供背景參考，不可取代文件原文：\n",
}


async def build_memory_context(
    intent: str,
    thread_id: str,
    user_id: str | None,
    query: str,
    document_ids: list[int] | None = None,
) -> dict[str, str | None]:
    """Return memory snippets keyed by type, per the intent's read policy.

    Callers (router/runner) inject the values into agent system messages.
    Returns empty dict if no memory is available.
    """
    policy = MEMORY_READ_POLICY.get(intent, MemoryRead())
    result: dict[str, str | None] = {}

    if policy.context_summary:
        try:
            import asyncio
            from services.memory_service import get_context_summary_text
            result["context_summary"] = await asyncio.to_thread(get_context_summary_text, thread_id)
        except Exception as exc:
            logger.debug("build_memory_context: context_summary failed: %s", exc)

    if policy.long_term and user_id:
        try:
            from services.memory_service import search_long_term_memory
            items = await search_long_term_memory(user_id, query)
            result["long_term"] = "\n".join(items) if items else None
        except Exception as exc:
            logger.debug("build_memory_context: long_term failed: %s", exc)

    if policy.doc_cache and document_ids:
        try:
            result["doc_cache"] = _load_doc_cache_summaries(document_ids)
        except Exception as exc:
            logger.debug("build_memory_context: doc_cache failed: %s", exc)

    return {k: v for k, v in result.items() if v}


def format_memory_system_messages(memory: dict[str, str | None]) -> list[str]:
    """Format memory dict into system message strings for agent injection."""
    messages = []
    for key in ("context_summary", "doc_cache", "long_term"):
        text = memory.get(key)
        if text:
            messages.append(_CONTEXT_LABELS.get(key, "") + text)
    return messages


async def record_agent_memory(
    intent: str,
    thread_id: str,
    user_id: str | None,
    question: str,
    result: AgentResult,
    document_ids: list[int] | None = None,
) -> None:
    """Write memory after an agent completes, per the intent's write policy."""
    policy = MEMORY_WRITE_POLICY.get(intent, MemoryWrite())

    if policy.context_summary:
        try:
            from services.memory_service import update_context_summary
            await update_context_summary(thread_id, question, result)
        except Exception as exc:
            logger.warning("record_agent_memory: context_summary write failed: %s", exc)

    if policy.long_term and user_id:
        try:
            from services.memory_service import store_long_term_memory
            await store_long_term_memory(user_id, thread_id, document_ids, question, result)
        except Exception as exc:
            logger.warning("record_agent_memory: long_term write failed: %s", exc)


def _load_doc_cache_summaries(document_ids: list[int]) -> str | None:
    """Load filled slot summaries from document_research_cache for context injection."""
    from db import db_session, DocumentResearchCache
    import json
    lines: list[str] = []
    try:
        with db_session() as db:
            rows = (
                db.query(
                    DocumentResearchCache.document_id,
                    DocumentResearchCache.slot_status,
                    DocumentResearchCache.evidence,
                )
                .filter(DocumentResearchCache.document_id.in_(document_ids))
                .all()
            )
            for doc_id, slot_status_raw, evidence_raw in rows:
                slot_status = (
                    json.loads(slot_status_raw)
                    if isinstance(slot_status_raw, str)
                    else (slot_status_raw or {})
                )
                evidence = (
                    json.loads(evidence_raw)
                    if isinstance(evidence_raw, str)
                    else (evidence_raw or {})
                )
                for slot, status in slot_status.items():
                    if status in ("FILLED", "PARTIAL"):
                        notes = evidence.get(slot, [])
                        if notes:
                            lines.append(f"- {slot.replace('_', ' ')}：{notes[0][:120]}")
    except Exception as exc:
        logger.debug("_load_doc_cache_summaries: %s", exc)
    return "\n".join(lines) if lines else None
