"""Durable Trace System v2 ingestion.

Events are first persisted in Postgres outbox rows. Redis is only used as a
wakeup queue so traces remain recoverable when Redis is disabled or restarted.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Iterable

from contextlib import contextmanager

from db import SessionLocal, TraceEventOutbox
from services.redis_service import redis_blpop, redis_enabled, redis_rpush
from services.trace_repositories import (
    ObservationRepository,
    ScoreRepository,
    TraceRepository,
)

logger = logging.getLogger(__name__)

TRACE_WAKEUP_QUEUE = "trace_ingestion:wakeup"
MAX_ATTEMPTS = 5
PROCESSING_TIMEOUT_SECONDS = 300

_worker_task: asyncio.Task | None = None
_stop_event: asyncio.Event | None = None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _json_text(value) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, default=str)


@contextmanager
def _worker_session():
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


class TraceEventIngestor:
    @staticmethod
    def enqueue_sync(events: Iterable[dict]) -> int:
        """Persist events to the durable outbox. Duplicate event_id is ignored."""
        rows = list(events)
        if not rows:
            return 0

        db = SessionLocal()
        written = 0
        try:
            for event in rows:
                event_id = event.get("event_id") or str(uuid.uuid4())
                event_type = event.get("event_type")
                body = event.get("body") or {}
                if not event_type:
                    continue
                exists = db.query(TraceEventOutbox.id).filter(TraceEventOutbox.event_id == event_id).first()
                if exists:
                    continue
                db.add(TraceEventOutbox(
                    event_id=event_id,
                    event_type=event_type,
                    body_json=_json_text(body),
                    status="pending",
                ))
                db.flush()
                written += 1
            db.commit()
            return written
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    async def enqueue(events: Iterable[dict]) -> int:
        written = await asyncio.to_thread(TraceEventIngestor.enqueue_sync, list(events))
        if written:
            await TraceEventIngestor.wakeup(written)
        return written

    @staticmethod
    async def wakeup(count: int = 1) -> None:
        if redis_enabled():
            await redis_rpush(TRACE_WAKEUP_QUEUE, {"count": count, "ts": _utcnow().isoformat()})


class TraceIngestionWorker:
    @staticmethod
    def process_pending(limit: int = 100) -> int:
        processed = 0
        with _worker_session() as db:
            # Recover stale locks first, commit separately so they're visible to the main query
            TraceIngestionWorker.recover_stale_processing(db)
            db.commit()

            # Single-transaction: SELECT FOR UPDATE SKIP LOCKED prevents concurrent workers
            # from processing the same event simultaneously
            rows = (
                db.query(TraceEventOutbox)
                .filter(
                    TraceEventOutbox.status.in_(["pending", "failed"]),
                    TraceEventOutbox.attempts < MAX_ATTEMPTS,
                )
                .order_by(TraceEventOutbox.created_at.asc())
                .limit(limit)
                .with_for_update(skip_locked=True)
                .all()
            )
            for row in rows:
                try:
                    row.attempts = (row.attempts or 0) + 1
                    row.status = "processing"
                    row.locked_at = _utcnow()
                    db.flush()

                    body = row.body_json
                    if isinstance(body, str):
                        body = json.loads(body)
                    TraceIngestionWorker._process_event(db, row.event_type, body)

                    row.status = "processed"
                    row.processed_at = _utcnow()
                    row.last_error = None
                    row.locked_at = None
                    row.locked_by = None
                    processed += 1
                    db.flush()
                except Exception as exc:
                    row.status = "failed"
                    row.last_error = str(exc)[:2000]
                    row.locked_at = None
                    row.locked_by = None
                    logger.warning("trace ingestion event failed event_id=%s: %s", row.event_id, exc)
                    db.flush()
            db.commit()
        return processed

    @staticmethod
    def recover_stale_processing(db) -> int:
        cutoff = _utcnow() - timedelta(seconds=PROCESSING_TIMEOUT_SECONDS)
        rows = (
            db.query(TraceEventOutbox)
            .filter(
                TraceEventOutbox.status == "processing",
                TraceEventOutbox.locked_at.isnot(None),
                TraceEventOutbox.locked_at < cutoff,
                TraceEventOutbox.attempts < MAX_ATTEMPTS,
            )
            .all()
        )
        for row in rows:
            row.status = "failed"
            row.last_error = "stale processing lock recovered"
            row.locked_at = None
            row.locked_by = None
        return len(rows)

    @staticmethod
    def _process_event(db, event_type: str, body: dict) -> None:
        if event_type == "trace-create":
            TraceRepository.upsert_trace(db, body)
        elif event_type in {"observation-create", "observation-update"}:
            ObservationRepository.upsert_observation(db, body)
        elif event_type == "score-create":
            # sync_legacy_cache=False: cache sync happens after all events in this
            # batch are processed and the session is about to commit, avoiding
            # stale reads on partial writes.
            ScoreRepository.upsert_score(db, body, sync_legacy_cache=False)
        else:
            raise ValueError(f"Unsupported trace event type: {event_type}")


async def _worker_loop() -> None:
    assert _stop_event is not None
    while not _stop_event.is_set():
        try:
            if redis_enabled():
                await redis_blpop(TRACE_WAKEUP_QUEUE, timeout=2)
            else:
                await asyncio.sleep(2)
            await asyncio.to_thread(TraceIngestionWorker.process_pending)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("trace ingestion worker tick failed: %s", exc)
            await asyncio.sleep(2)


def init_trace_ingestion_worker() -> None:
    global _worker_task, _stop_event
    if _worker_task is not None and not _worker_task.done():
        return
    _stop_event = asyncio.Event()
    _worker_task = asyncio.create_task(_worker_loop())


async def stop_trace_ingestion_worker() -> None:
    global _worker_task, _stop_event
    if _stop_event is not None:
        _stop_event.set()
    if _worker_task is not None:
        _worker_task.cancel()
        await asyncio.gather(_worker_task, return_exceptions=True)
    _worker_task = None
    _stop_event = None
