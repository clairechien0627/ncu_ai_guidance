"""Conversation memory service.

Short-term:  context_summary column in conversations table.
             Mini LLM compresses each research result into a running summary.
             Injected into chat/research agent calls as background context.

Long-term:   Qdrant 'research_memories' collection.
             Each research finding is embedded and stored with user_id/thread_id.
             Semantically similar past findings are retrieved for new questions.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from config import settings

if TYPE_CHECKING:
    from agents.types import AgentResult

logger = logging.getLogger(__name__)

MEMORY_COLLECTION = "research_memories"
VECTOR_SIZE = 3072          # text-embedding-3-large
MAX_FINDINGS = 5            # max findings to keep in context_summary
FINDING_MAX_CHARS = 200     # per-finding summary length cap
LONG_TERM_LIMIT = 2         # findings injected from long-term per call


# ── LLM / embedding singletons ───────────────────────────────────────────────

def _mini_llm():
    from langchain_openai import AzureChatOpenAI
    return AzureChatOpenAI(
        azure_deployment=settings.azure_mini_deployment or settings.azure_chat_deployment,
        azure_endpoint=settings.azure_openai_endpoint,
        api_key=settings.azure_openai_api_key.get_secret_value(),
        api_version=settings.azure_openai_api_version,
        temperature=0,
    )


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


# ── Short-term: context_summary ───────────────────────────────────────────────

def _load_summary(thread_id: str) -> dict:
    """Read current context_summary JSON from DB."""
    from db import db_session, Conversation
    with db_session() as db:
        row = db.query(Conversation.context_summary).filter(
            Conversation.id == int(thread_id)
        ).first()
        if row and row[0]:
            return json.loads(row[0])
    except Exception:
        pass
    return {"version": 1, "findings": [], "user_focus": ""}


def _save_summary(thread_id: str, data: dict) -> None:
    from db import db_session, Conversation
    with db_session() as db:
        conv = db.query(Conversation).filter(Conversation.id == int(thread_id)).first()
        if conv:
            conv.context_summary = json.dumps(data, ensure_ascii=False)
            db.commit()
    except Exception as exc:
        logger.warning("_save_summary failed: %s", exc)
        db.rollback()


async def update_context_summary(
    thread_id: str,
    question: str,
    result: AgentResult,
) -> None:
    """Compress research result into context_summary. Fire-and-forget safe."""
    try:
        existing = _load_summary(thread_id)
        existing_json = json.dumps(existing, ensure_ascii=False)

        from langchain_core.messages import SystemMessage, HumanMessage
        llm = _mini_llm()
        prompt = (
            "你是對話記憶壓縮器。根據現有記憶和新的研究結果，更新記憶摘要。\n\n"
            "規則：\n"
            f"- findings 最多保留 {MAX_FINDINGS} 筆，每筆 summary 不超過 {FINDING_MAX_CHARS} 字\n"
            "- user_focus 從用戶的問題推斷（30字以內）\n"
            "- 若主題重複，合併而非新增\n"
            "- 只回傳 JSON，不要其他說明\n\n"
            f"現有記憶：{existing_json}\n\n"
            f"用戶問題：{question}\n\n"
            f"研究結果摘要：{result.response[:2000]}"
        )
        response = await llm.ainvoke([
            SystemMessage(content="你是精確的 JSON 產生器，只輸出有效的 JSON。"),
            HumanMessage(content=prompt),
        ])
        text = response.content.strip()
        # Strip markdown code fences if present
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        updated = json.loads(text)
        updated.setdefault("version", 1)
        updated.setdefault("findings", [])
        updated["last_updated"] = datetime.now(timezone.utc).isoformat()
        _save_summary(thread_id, updated)
    except Exception as exc:
        logger.warning("update_context_summary failed: %s", exc)


def get_context_summary_text(thread_id: str) -> str | None:
    """Format context_summary as prose for injection into _build_messages."""
    try:
        data = _load_summary(thread_id)
        findings = data.get("findings", [])
        if not findings:
            return None
        lines = []
        for f in findings[-(MAX_FINDINGS):]:
            summary = (f.get("summary") or "").strip()
            if summary:
                sources = "、".join(f.get("sources", [])[:2])
                lines.append(f"- {summary}" + (f"（{sources}）" if sources else ""))
        if data.get("user_focus"):
            lines.append(f"用戶關注：{data['user_focus']}")
        return "\n".join(lines) if lines else None
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
