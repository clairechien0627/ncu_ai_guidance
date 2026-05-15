"""Conversation memory service.

Short-term:  context_summary column in conversations table (v2 structured format).
             Research coverage results are stored directly from AgentResult.coverage_result
             without LLM compression — faster, accurate, and preserves slot structure.
             Injected into chat/research agent calls as background context.

Long-term:   Qdrant 'research_memories' collection.
             Each research finding is embedded and stored with user_id/thread_id.
             Semantically similar past findings are retrieved for new questions.

Document:    document_research_cache table (per-document, per-coverage-template).
             Slots from previous successful research are pre-loaded on next run,
             allowing the research graph to skip already-filled coverage items.
"""
from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone, timedelta
from typing import TYPE_CHECKING

from config import settings

if TYPE_CHECKING:
    from agents.types import AgentResult

logger = logging.getLogger(__name__)

MEMORY_COLLECTION = "research_memories"
VECTOR_SIZE = 3072          # text-embedding-3-large
MAX_FINDINGS = 5            # max findings to keep in context_summary
LONG_TERM_LIMIT = 2         # findings injected from long-term per call
CACHE_MAX_AGE_DAYS = 30     # document research cache TTL


# ── Embedding singleton ───────────────────────────────────────────────────────

def _embeddings():
    from langchain_openai import AzureOpenAIEmbeddings
    return AzureOpenAIEmbeddings(
        azure_deployment=settings.azure_embedding_deployment,
        azure_endpoint=settings.azure_openai_endpoint,
        api_key=settings.azure_openai_api_key.get_secret_value(),
        api_version=settings.azure_openai_api_version,
    )


# ── Qdrant collection init ────────────────────────────────────────────────────

def ensure_memory_collection() -> None:
    """Create research_memories collection if it doesn't exist. Called at startup."""
    try:
        from rag.store import get_qdrant_client
        from qdrant_client.models import Distance, VectorParams
        client = get_qdrant_client()
        if not client.collection_exists(MEMORY_COLLECTION):
            client.create_collection(
                collection_name=MEMORY_COLLECTION,
                vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
            )
            logger.info("Created Qdrant collection: %s", MEMORY_COLLECTION)
    except Exception as exc:
        logger.warning("ensure_memory_collection failed (non-fatal): %s", exc)


# ── Coverage hash ─────────────────────────────────────────────────────────────

def _coverage_hash(coverage_ids: list[str]) -> str:
    return hashlib.md5("|".join(sorted(coverage_ids)).encode()).hexdigest()


# ── Short-term: context_summary ───────────────────────────────────────────────

def _load_summary(thread_id: str) -> dict:
    from db import db_session, Conversation
    try:
        with db_session() as db:
            row = db.query(Conversation.context_summary).filter(
                Conversation.id == int(thread_id)
            ).first()
            if row and row[0]:
                return json.loads(row[0])
    except Exception:
        pass
    return {"version": 2, "findings": [], "user_focus": ""}


def _save_summary(thread_id: str, data: dict) -> None:
    from db import db_session, Conversation
    try:
        with db_session() as db:
            conv = db.query(Conversation).filter(Conversation.id == int(thread_id)).first()
            if conv:
                conv.context_summary = json.dumps(data, ensure_ascii=False)
                db.commit()
    except Exception as exc:
        logger.warning("_save_summary failed: %s", exc)


_STATUS_LABEL = {"FILLED": "✓", "PARTIAL": "△", "EXHAUSTED": "—", "NOT_FILLED": "?"}


async def update_context_summary(
    thread_id: str,
    question: str,
    result: AgentResult,
) -> None:
    """Update context_summary directly from structured coverage_result. No LLM call."""
    coverage = result.coverage_result
    if not coverage:
        return

    existing = _load_summary(thread_id)
    findings = list(existing.get("findings", []))

    new_finding = {
        "question": question[:200],
        "coverage": {
            slot_id: {
                "status": slot_data["status"],
                "label": slot_data.get("label", slot_id),
                "notes": [n[:150] for n in (slot_data.get("notes") or [])[:2]],
            }
            for slot_id, slot_data in coverage.items()
            if slot_data.get("status") in ("FILLED", "PARTIAL", "EXHAUSTED")
        },
        "sources": list(result.sources)[:5],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    if not new_finding["coverage"]:
        return

    # Deduplicate by question, keep last MAX_FINDINGS
    findings = [f for f in findings if f.get("question", "")[:100] != question[:100]]
    findings = findings[-(MAX_FINDINGS - 1):]
    findings.append(new_finding)

    user_focus = question[:60]

    _save_summary(thread_id, {
        "version": 2,
        "user_focus": user_focus,
        "findings": findings,
        "last_updated": datetime.now(timezone.utc).isoformat(),
    })


def get_context_summary_text(thread_id: str) -> str | None:
    """Format context_summary as structured prose for injection into _build_messages."""
    try:
        data = _load_summary(thread_id)
        findings = data.get("findings", [])
        if not findings:
            return None

        version = data.get("version", 1)

        if version >= 2:
            lines: list[str] = []
            for finding in findings[-MAX_FINDINGS:]:
                q = (finding.get("question") or "").strip()
                if q:
                    lines.append(f"問題：{q[:120]}")
                for slot_id, slot_data in (finding.get("coverage") or {}).items():
                    status = slot_data.get("status", "")
                    label = slot_data.get("label", slot_id)
                    notes = slot_data.get("notes") or []
                    marker = _STATUS_LABEL.get(status, "?")
                    note_text = notes[0][:100] if notes else f"（{status.lower()}）"
                    lines.append(f"  {marker} {label}：{note_text}")
                srcs = "、".join((finding.get("sources") or [])[:3])
                if srcs:
                    lines.append(f"  來源：{srcs}")
            if data.get("user_focus"):
                lines.append(f"用戶關注：{data['user_focus'][:60]}")
            return "\n".join(lines) if lines else None

        # v1 fallback: original prose format
        lines_v1: list[str] = []
        for f in findings[-(MAX_FINDINGS):]:
            summary = (f.get("summary") or "").strip()
            if summary:
                sources = "、".join(f.get("sources", [])[:2])
                lines_v1.append(f"- {summary}" + (f"（{sources}）" if sources else ""))
        if data.get("user_focus"):
            lines_v1.append(f"用戶關注：{data['user_focus']}")
        return "\n".join(lines_v1) if lines_v1 else None
    except Exception:
        return None


# ── Long-term: Qdrant research_memories ──────────────────────────────────────

async def store_research_memory(
    user_id: str,
    thread_id: str,
    document_ids: list[int] | None,
    question: str,
    result: AgentResult,
) -> None:
    """Embed and store a research finding in long-term memory. Fire-and-forget safe."""
    if not user_id:
        return
    try:
        text_to_embed = f"問題：{question}\n重點：{result.response[:1500]}"
        emb = _embeddings()
        vector = await emb.aembed_query(text_to_embed)

        from rag.store import get_qdrant_client
        from qdrant_client.models import PointStruct
        client = get_qdrant_client()
        point = PointStruct(
            id=str(uuid.uuid4()),
            vector=vector,
            payload={
                "user_id": user_id,
                "thread_id": thread_id,
                "document_ids": document_ids or [],
                "question": question[:300],
                "summary": result.response[:1000],
                "sources": result.sources[:5],
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        client.upsert(collection_name=MEMORY_COLLECTION, points=[point])
    except Exception as exc:
        logger.warning("store_research_memory failed: %s", exc)


async def search_research_memories(
    user_id: str,
    query: str,
    limit: int = LONG_TERM_LIMIT,
) -> list[str]:
    """Return relevant past research findings for the current question."""
    if not user_id:
        return []
    try:
        emb = _embeddings()
        vector = await emb.aembed_query(query)

        from rag.store import get_qdrant_client
        from qdrant_client.models import Filter, FieldCondition, MatchValue
        client = get_qdrant_client()
        results = client.search(
            collection_name=MEMORY_COLLECTION,
            query_vector=vector,
            query_filter=Filter(must=[
                FieldCondition(key="user_id", match=MatchValue(value=user_id))
            ]),
            limit=limit,
            score_threshold=0.75,
        )
        return [
            f"（過去研究）{r.payload.get('question', '')}：{r.payload.get('summary', '')[:200]}"
            for r in results
            if r.payload.get("summary")
        ]
    except Exception as exc:
        logger.warning("search_research_memories failed: %s", exc)
        return []


# ── Document research cache ────────────────────────────────────────────────────

def load_document_research_cache(
    document_id: int,
    coverage_ids: list[str],
    max_age_days: int = CACHE_MAX_AGE_DAYS,
) -> dict | None:
    """Return cached slot_status, evidence, and keywords for a document, or None.

    Only returns a cache hit if:
    - The coverage template (coverage_ids) matches exactly.
    - The cache is not older than max_age_days.
    - At least one slot is FILLED or EXHAUSTED (partial runs are not cached).
    """
    from db import db_session, DocumentResearchCache
    chash = _coverage_hash(coverage_ids)
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    try:
        with db_session() as db:
            row = (
                db.query(DocumentResearchCache)
                .filter(
                    DocumentResearchCache.document_id == document_id,
                    DocumentResearchCache.coverage_hash == chash,
                    DocumentResearchCache.updated_at >= cutoff,
                )
                .first()
            )
            if row is None:
                return None

            slot_status = json.loads(row.slot_status) if isinstance(row.slot_status, str) else (row.slot_status or {})
            useful = any(v in ("FILLED", "EXHAUSTED") for v in slot_status.values())
            if not useful:
                return None

            def _load(col) -> dict:
                if col is None:
                    return {}
                return json.loads(col) if isinstance(col, str) else (col or {})

            def _load_list(col) -> list:
                if col is None:
                    return []
                return json.loads(col) if isinstance(col, str) else (col or [])

            return {
                "slot_status": slot_status,
                "evidence": _load(row.evidence),
                "evidence_details": _load(row.evidence_details),
                "known_keywords": _load_list(row.known_keywords),
                "avoid_query_terms": _load_list(row.avoid_query_terms),
                "sources": _load_list(row.sources),
            }
    except Exception as exc:
        logger.debug("load_document_research_cache(%d): %s", document_id, exc)
        return None


def save_document_research_cache(
    *,
    document_id: int,
    coverage_ids: list[str],
    slot_status: dict,
    evidence: dict,
    evidence_details: dict,
    known_keywords: list[str],
    avoid_query_terms: list[str],
    sources: list[str],
    search_count: int,
) -> None:
    """Upsert research results for a document. Fire-and-forget safe."""
    from db import db_session, DocumentResearchCache
    chash = _coverage_hash(coverage_ids)
    now = datetime.now(timezone.utc)
    try:
        with db_session() as db:
            row = (
                db.query(DocumentResearchCache)
                .filter(
                    DocumentResearchCache.document_id == document_id,
                    DocumentResearchCache.coverage_hash == chash,
                )
                .first()
            )
            payload = {
                "slot_status": json.dumps(slot_status, ensure_ascii=False),
                "evidence": json.dumps(evidence, ensure_ascii=False),
                "evidence_details": json.dumps(evidence_details, ensure_ascii=False),
                "known_keywords": json.dumps(known_keywords, ensure_ascii=False),
                "avoid_query_terms": json.dumps(avoid_query_terms, ensure_ascii=False),
                "sources": json.dumps(sources, ensure_ascii=False),
                "search_count": search_count,
                "updated_at": now,
            }
            if row is None:
                db.add(DocumentResearchCache(
                    document_id=document_id,
                    coverage_hash=chash,
                    created_at=now,
                    **payload,
                ))
            else:
                for k, v in payload.items():
                    setattr(row, k, v)
            db.commit()
    except Exception as exc:
        logger.warning("save_document_research_cache(%d): %s", document_id, exc)
