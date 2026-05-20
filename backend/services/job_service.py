import asyncio
import datetime
import json
import logging
import os
import threading
import time
from uuid import uuid4

from services.redis_service import (
    BROADCAST_CHANNEL, EVENTS_CHANNEL,
    QUEUE_REINDEX, QUEUE_EXTRACT, QUEUE_PARSE,
    get_redis, redis_enabled, redis_publish, redis_rpush, redis_blpop,
    redis_remove_from_queue, redis_reorder_queue,
)

logger = logging.getLogger(__name__)

# ── 架構說明：單機設計（Single-Instance Only）────────────────────────────────────
# 此模組使用 in-memory state 作為執行期 source of truth：
#   _active_jobs        — 所有 job 的即時狀態，供 SSE 廣播用
#   _reindex_tasks      — doc_id → asyncio.Task，供取消操作用（無法序列化）
#   _*_work_queue       — 各類型待執行的工作項目
#   _reindex_event      — asyncio.Event，通知 reindex worker 醒來
#   _extract_condition  — asyncio.Condition，通知 extract worker 醒來
#   _parse_condition    — asyncio.Condition，通知 parse worker 醒來
#
# JobHistory（DB）只用於啟動後恢復（restore_jobs_from_db），不是執行期 source of truth。
# _persist_job() 做 best-effort 非同步寫入，DB 掉了 job 仍能繼續執行。
#
# ✅ Redis 支援（選用，需設定 REDIS_URL）：
#   - _push_jobs_payload() 同時 PUBLISH 到 Redis channel（SSE 跨 instance 廣播）
#   - 工作佇列改用 Redis LIST（RPUSH 入隊 / BLPOP 出隊），支援跨 instance 任務分配
#   - 取消信號透過 Redis Pub/Sub 廣播到所有 instance
#   - _reindex_tasks 仍為 in-memory（asyncio.Task 無法序列化）
#   - REDIS_URL 未設定時行為與原本完全相同（in-memory fallback）
# ─────────────────────────────────────────────────────────────────────────────

# ── In-memory job registry ──────────────────────────────────────────────────────
# _jobs_lock guards ALL mutations of _active_jobs.
# Rule: hold the lock only for in-memory operations; never call _persist_job
# (DB I/O) while holding the lock — collect snapshots inside, write outside.
_jobs_lock = threading.Lock()
_active_jobs: list[dict] = []
_job_subscribers: list[asyncio.Queue] = []
_reindex_tasks: dict[int, asyncio.Task] = {}
_extract_tasks: dict[int, asyncio.Task] = {}
_worker_tasks: list[asyncio.Task] = []  # background worker handles for shutdown

_reindex_work_queue: list[dict] = []
_extract_work_queue: list[dict] = []
_parse_work_queue: list[dict] = []
_reindex_event: asyncio.Event | None = None
_extract_condition: asyncio.Condition | None = None
_parse_condition: asyncio.Condition | None = None
_TERMINAL_STATUSES = {"done", "error", "cancelled"}


def _restore_stage_log(row) -> list[str]:
    """Restore stage_log from DB row; fall back to [row.stage] for old rows."""
    raw = getattr(row, "stage_log", None)
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list) and parsed:
                return parsed
        except Exception:
            pass
    return [row.stage] if row.stage else []


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


def _iso(dt: datetime.datetime | None = None) -> str:
    return (dt or _utcnow()).isoformat() + "Z"


def _dt_to_iso(dt: datetime.datetime | None) -> str | None:
    """Convert a DB datetime (naive UTC or timezone-aware) to a UTC ISO 'Z' string.

    PostgreSQL may return timezone-aware datetimes if the column is TIMESTAMPTZ
    or if the server TimeZone is set to a non-UTC zone.  Normalise everything
    to a plain UTC value before serialising so the frontend always sees UTC.
    """
    if dt is None:
        return None
    if dt.tzinfo is not None:
        # Convert any tz-aware datetime to UTC, then strip tzinfo
        import datetime as _dt
        dt = dt.astimezone(_dt.timezone.utc).replace(tzinfo=None)
    return dt.isoformat() + "Z"


def _new_job_id(job_type: str, doc_id: int) -> str:
    return f"{job_type}:{doc_id}:{uuid4().hex[:10]}"


def _is_active_status(status: str | None) -> bool:
    return (status or "queued") not in _TERMINAL_STATUSES


def _persist_job(job: dict, *, final_status: str | None = None) -> None:
    """Best-effort durable job snapshot.

    The in-memory queue remains the execution source of truth. JobHistory stores
    enough state for the UI/API to survive process restarts and show stale
    running jobs instead of losing them.
    """
    try:
        from db import db_session, JobHistory
    except Exception:
        return

    job_id = str(job.get("job_id") or "")
    if not job_id:
        job_id = _new_job_id(str(job.get("job_type") or "job"), int(job.get("doc_id") or 0))
        job["job_id"] = job_id

    now = _utcnow()
    status = final_status or str(job.get("status") or "queued")
    try:
        with db_session() as s:
            row = s.query(JobHistory).filter(JobHistory.job_id == job_id).first()
            if not row:
                row = JobHistory(
                    job_id=job_id,
                    doc_id=int(job.get("doc_id") or 0),
                    filename=str(job.get("filename") or job.get("doc_id") or ""),
                    job_type=str(job.get("job_type") or "job"),
                    started_at=now if status in ("running", "done", "error") else None,
                )
                s.add(row)
            row.doc_id = int(job.get("doc_id") or row.doc_id or 0)
            row.filename = str(job.get("filename") or row.filename or row.doc_id)
            row.job_type = str(job.get("job_type") or row.job_type or "job")
            row.status = status
            row.stage = job.get("stage")
            row.stage_log = json.dumps(job.get("stage_log") or [], ensure_ascii=False)
            row.error = job.get("error")
            if status == "running" and not row.started_at:
                row.started_at = now
            row.updated_at = now
            if status in ("done", "error", "cancelled"):
                row.completed_at = now
            s.commit()
    except Exception as e:
        logger.debug("Failed to persist job snapshot: %s", e)


def _persist_doc_job(doc_id: int) -> None:
    for job in _active_jobs:
        if job.get("doc_id") == doc_id:
            _persist_job(job)
            return


# ── Push helpers ────────────────────────────────────────────────────────────────

async def push_jobs():
    data = json.dumps(_active_jobs)
    await _push_jobs_payload(data)


async def _push_jobs_payload(data: str):
    for q in list(_job_subscribers):
        try:
            q.put_nowait(data)
        except asyncio.QueueFull:
            pass
    # Broadcast to all instances via Redis Pub/Sub (no-op when Redis not configured)
    await redis_publish(BROADCAST_CHANNEL, data)


def _jobs_payload() -> str:
    return json.dumps(_active_jobs)


async def enqueue_reindex_job(item: dict) -> None:
    """Add a reindex job to the work queue (local + Redis when available)."""
    if redis_enabled():
        await redis_rpush(QUEUE_REINDEX, item)
    else:
        _reindex_work_queue.append(item)
        if _reindex_event is not None:
            _reindex_event.set()


# ── Job state mutations ──────────────────────────────────────────────────────────

def jobs_enqueue(pairs: list[tuple[int, str]], job_type: str = "reindex"):
    global _active_jobs
    to_persist = []
    with _jobs_lock:
        for doc_id, filename in pairs:
            if any(j["doc_id"] == doc_id and _is_active_status(j.get("status")) for j in _active_jobs):
                continue
            _active_jobs = [j for j in _active_jobs if j["doc_id"] != doc_id]
            job = {
                "job_id": _new_job_id(job_type, doc_id),
                "doc_id": doc_id,
                "filename": filename,
                "status": "queued",
                "job_type": job_type,
                "stage_log": [],
                "updated_at": _iso(),
            }
            _active_jobs.append(job)
            to_persist.append(dict(job))
    for job in to_persist:
        _persist_job(job)


def jobs_set_running(doc_id: int):
    job_snap = None
    with _jobs_lock:
        for job in _active_jobs:
            if job["doc_id"] == doc_id and job["status"] == "queued":
                job["status"] = "running"
                job["started_at"] = job.get("started_at") or _iso()
                job["updated_at"] = _iso()
                job_snap = dict(job)
                break
    if job_snap:
        _persist_job(job_snap)


def jobs_remove(doc_id: int):
    global _active_jobs
    to_persist = []
    with _jobs_lock:
        for job in _active_jobs:
            if job.get("doc_id") == doc_id and _is_active_status(job.get("status")):
                job["status"] = "cancelled"
                job["stage"] = "已取消"
                job["updated_at"] = _iso()
                to_persist.append(dict(job))
        if to_persist:
            _active_jobs = [j for j in _active_jobs if j["doc_id"] != doc_id]
    for snap in to_persist:
        _persist_job(snap, final_status="cancelled")


def jobs_set_stage(doc_id: int, stage: str):
    job_snap = None
    with _jobs_lock:
        for job in _active_jobs:
            if job["doc_id"] == doc_id and _is_active_status(job.get("status")):
                job["stage"] = stage
                stage_log = list(job.get("stage_log") or [])
                if not stage_log or stage_log[-1] != stage:
                    stage_log.append(stage)
                job["stage_log"] = stage_log[-100:]
                job["updated_at"] = _iso()
                job_snap = dict(job)
                break
    if job_snap:
        _persist_job(job_snap)


def jobs_mark_done(doc_id: int, error: str | None = None):
    from db import db_session, Document, JobHistory
    job_type = "reindex"
    filename = str(doc_id)
    found = False
    job_snap = None
    with _jobs_lock:
        for job in _active_jobs:
            if job["doc_id"] == doc_id and _is_active_status(job.get("status")):
                job["status"] = "done"
                job["stage"] = "失敗" if error else "完成"
                stage_log = list(job.get("stage_log") or [])
                if not stage_log or stage_log[-1] != job["stage"]:
                    stage_log.append(job["stage"])
                job["stage_log"] = stage_log[-100:]
                job["completed_at"] = _iso()
                job["updated_at"] = job["completed_at"]
                if error:
                    job["error"] = error[:200]
                job_type = job.get("job_type", "reindex")
                filename = job.get("filename", str(doc_id))
                found = True
                job_snap = dict(job)
                break
    if job_snap:
        _persist_job(job_snap, final_status="error" if error else "done")
    if not found or filename == str(doc_id):
        try:
            with db_session() as s:
                doc = s.query(Document).filter(Document.id == doc_id).first()
                if doc:
                    filename = doc.filename
        except Exception:
            pass
    logger.info("jobs_mark_done(%d): found=%s", doc_id, found)
    try:
        with db_session() as s:
            if not found:
                now = _utcnow()
                s.add(JobHistory(
                    job_id=_new_job_id(job_type, doc_id),
                    doc_id=doc_id,
                    filename=filename,
                    job_type=job_type,
                    status="error" if error else "done",
                    stage="失敗" if error else "完成",
                    error=error[:200] if error else None,
                    updated_at=now,
                    completed_at=now,
                ))
            s.commit()
    except Exception as e:
        logger.warning("Failed to write JobHistory for doc %d: %s", doc_id, e)


def jobs_has_active(doc_id: int) -> bool:
    return any(j["doc_id"] == doc_id and _is_active_status(j.get("status")) for j in _active_jobs)


def jobs_append(entry: dict):
    entry.setdefault("job_id", _new_job_id(str(entry.get("job_type") or "job"), int(entry.get("doc_id") or 0)))
    entry.setdefault("updated_at", _iso())
    with _jobs_lock:
        _active_jobs.append(entry)
    _persist_job(entry)


def jobs_has_active_parse(doc_id: int, parser: str) -> bool:
    job_type = f"parse_{parser}"
    return any(
        j["doc_id"] == doc_id
        and j.get("job_type") == job_type
        and _is_active_status(j.get("status"))
        for j in _active_jobs
    )


def jobs_set_parse_running(job_id: str):
    snap = None
    with _jobs_lock:
        for j in _active_jobs:
            if j.get("job_id") == job_id and j["status"] == "queued":
                j["status"] = "running"
                j["started_at"] = j.get("started_at") or _iso()
                j["updated_at"] = _iso()
                snap = dict(j)
                break
    if snap:
        _persist_job(snap)


def jobs_finalize_parse(job_id: str):
    snap = None
    with _jobs_lock:
        for j in _active_jobs:
            if j.get("job_id") == job_id:
                j["status"] = "done"
                j["stage"] = "完成"
                j["completed_at"] = _iso()
                j["updated_at"] = j["completed_at"]
                snap = dict(j)
                break
    if snap:
        _persist_job(snap, final_status="done")


def jobs_clear_done():
    global _active_jobs
    with _jobs_lock:
        _active_jobs = [j for j in _active_jobs if _is_active_status(j.get("status"))]


async def jobs_reorder_and_sync(doc_id: int, new_index: int) -> bool:
    global _active_jobs
    with _jobs_lock:
        job = next((j for j in _active_jobs if j["doc_id"] == doc_id and _is_active_status(j.get("status"))), None)
        if not job:
            return False
        _active_jobs = [j for j in _active_jobs if j is not job]
        non_done = [j for j in _active_jobs if _is_active_status(j.get("status"))]
        done_entries = [j for j in _active_jobs if not _is_active_status(j.get("status"))]
        new_index = max(0, min(new_index, len(non_done)))
        non_done.insert(new_index, job)
        _active_jobs = done_entries + non_done

    queued_reindex = [j["doc_id"] for j in _active_jobs if j["status"] == "queued" and j.get("job_type") == "reindex"]
    _reindex_work_queue.sort(key=lambda x: queued_reindex.index(x["doc_id"]) if x["doc_id"] in queued_reindex else 999)

    queued_extract = [
        j["doc_id"]
        for j in _active_jobs
        if j["status"] == "queued" and str(j.get("job_type", "")).startswith("extract")
    ]
    if _extract_condition is not None:
        async with _extract_condition:
            _extract_work_queue.sort(key=lambda x: queued_extract.index(x["doc_id"]) if x["doc_id"] in queued_extract else 999)

    queued_parse_jobs = [
        j for j in _active_jobs
        if j["status"] == "queued" and str(j.get("job_type", "")).startswith("parse_")
    ]
    queued_parse_ids = [j.get("job_id") for j in queued_parse_jobs]
    if _parse_condition is not None:
        async with _parse_condition:
            _parse_work_queue.sort(key=lambda x: queued_parse_ids.index(x["job_id"]) if x["job_id"] in queued_parse_ids else 999)

    # Reorder Redis work queues to match
    if redis_enabled():
        await redis_reorder_queue(QUEUE_REINDEX, doc_id, new_index)

    return True


async def jobs_cancel_queued(doc_id: int) -> str:
    """Returns 'running', 'queued', or 'not_found'."""
    if not jobs_has_active(doc_id):
        return "not_found"
    running_task = next(
        (t for t in (_reindex_tasks.get(doc_id), _extract_tasks.get(doc_id))
         if t and not t.done()),
        None,
    )
    if running_task:
        running_task.cancel()
        for job in _active_jobs:
            if job.get("doc_id") == doc_id:
                job["stage"] = "取消中"
                job["updated_at"] = _iso()
                _persist_job(job)
                break
        if redis_enabled():
            await redis_publish(EVENTS_CHANNEL, json.dumps({"type": "cancel", "doc_id": doc_id}))
        return "running"
    _reindex_work_queue[:] = [x for x in _reindex_work_queue if x["doc_id"] != doc_id]
    if _extract_condition is not None:
        async with _extract_condition:
            _extract_work_queue[:] = [x for x in _extract_work_queue if x["doc_id"] != doc_id]
    if _parse_condition is not None:
        async with _parse_condition:
            _parse_work_queue[:] = [x for x in _parse_work_queue if x["doc_id"] != doc_id]
    # Remove from Redis queues + signal other instances to cancel
    if redis_enabled():
        for key in (QUEUE_REINDEX, QUEUE_EXTRACT, QUEUE_PARSE):
            await redis_remove_from_queue(key, doc_id)
        await redis_publish(EVENTS_CHANNEL, json.dumps({"type": "cancel", "doc_id": doc_id}))
    jobs_remove(doc_id)
    await push_jobs()
    return "queued"


def get_jobs() -> list[dict]:
    return list(_active_jobs)


def restore_jobs_from_db(limit: int = 200) -> None:
    """Restore recent job snapshots for the job drawer after process restart."""
    try:
        from db import db_session, Document, JobHistory
    except Exception:
        return

    restored: list[dict] = []
    try:
        with db_session() as s:
            rows = (
                s.query(JobHistory, Document.filename.label("doc_filename"))
                .outerjoin(Document, JobHistory.doc_id == Document.id)
                .order_by(JobHistory.updated_at.desc().nullslast(), JobHistory.completed_at.desc())
                .limit(limit)
                .all()
            )
            seen_jobs: set[str] = set()
            for row, doc_filename in rows:
                key = row.job_id or f"{row.doc_id}:{row.job_type}:{row.id}"
                if key in seen_jobs:
                    continue
                seen_jobs.add(key)
                status = row.status or "done"
                stale = False
                if status in ("queued", "running"):
                    status = "error"
                    stale = True
                entry = {
                    "job_id": row.job_id or key,
                    "doc_id": row.doc_id,
                    "filename": doc_filename or row.filename,
                    "job_type": row.job_type,
                    "status": status,
                    "stage": "重啟前工作未完成" if stale else (row.stage or ("完成" if status == "done" else "")),
                    "error": row.error or ("服務重啟後此工作未自動續跑，請重新排程。" if stale else None),
                    "started_at": _dt_to_iso(row.started_at),
                    "updated_at": _dt_to_iso(row.updated_at),
                    "completed_at": _dt_to_iso(row.completed_at),
                    "stage_log": _restore_stage_log(row),
                }
                restored.append({k: v for k, v in entry.items() if v is not None})
    except Exception as e:
        logger.warning("Failed to restore jobs from DB: %s", e)
        return

    with _jobs_lock:
        _active_jobs[:] = list(reversed(restored))


# ── DB helpers (called from thread) ─────────────────────────────────────────────

def _upsert_extraction(s, doc_id: int) -> "DocumentExtraction":
    """Get or create the DocumentExtraction row for doc_id (version=1)."""
    from db import DocumentExtraction
    ext = s.query(DocumentExtraction).filter(
        DocumentExtraction.document_id == doc_id,
        DocumentExtraction.version == 1,
    ).first()
    if ext is None:
        ext = DocumentExtraction(document_id=doc_id, version=1)
        s.add(ext)
    return ext


def db_set_summarized(doc_id: int, summary: dict):
    from db import db_session, Document
    with db_session() as s:
        ext = _upsert_extraction(s, doc_id)
        summary_str = json.dumps(summary, ensure_ascii=False)
        tags_str = json.dumps(summary.get("tags", []), ensure_ascii=False)
        ext.summary_json = summary_str
        ext.tags = tags_str
        d = s.query(Document).filter(Document.id == doc_id).first()
        if d:
            d.batch_status = "summarized"
        s.commit()


def db_set_research_step1(doc_id: int, answer: str, sources: list[str], observation_id: str | None = None):
    from db import db_session, Document
    with db_session() as s:
        d = s.query(Document).filter(Document.id == doc_id).first()
        ext = _upsert_extraction(s, doc_id)
        sources_str = json.dumps(sources, ensure_ascii=False)
        ext.raw_research_answer = answer
        ext.raw_research_sources = sources_str
        if observation_id:
            ext.research_observation_id = observation_id
        d.batch_status = "summarized" if ext.summary_json else "pending"
        s.commit()


def db_get_research_step1(doc_id: int) -> str | None:
    from db import db_session, DocumentExtraction
    with db_session() as s:
        ext = s.query(DocumentExtraction).filter(
            DocumentExtraction.document_id == doc_id,
            DocumentExtraction.version == 1,
        ).first()
        return ext.raw_research_answer if ext else None


def _merged_summary(existing_json: str | None, patch: dict) -> dict:
    base = json.loads(existing_json) if existing_json else {
        "motivation": "資料不足",
        "method": "資料不足",
        "results": "資料不足",
        "tags": [],
    }
    base.update(patch)
    return base


def db_set_summary_patch(doc_id: int, patch: dict):
    from db import db_session, Document
    with db_session() as s:
        d = s.query(Document).filter(Document.id == doc_id).first()
        ext = _upsert_extraction(s, doc_id)
        summary = _merged_summary(ext.summary_json, patch)
        summary_str = json.dumps(summary, ensure_ascii=False)
        tags_str = json.dumps(summary.get("tags", []), ensure_ascii=False)
        ext.summary_json = summary_str
        ext.tags = tags_str
        d.batch_status = "summarized"
        s.commit()


def db_set_error(doc_id: int, err: str):
    from db import db_session, Document
    with db_session() as s:
        d = s.query(Document).filter(Document.id == doc_id).first()
        d.batch_status = "error"
        d.error_message = err[:500]
        s.commit()


def db_restore_summarized(doc_id: int, err: str):
    from db import db_session, Document
    with db_session() as s:
        d = s.query(Document).filter(Document.id == doc_id).first()
        d.batch_status = "summarized"
        d.error_message = f"重建失敗: {err[:400]}"
        s.commit()


def reset_stuck_processing():
    from db import db_session, Document
    with db_session() as db:
        stuck = db.query(Document).filter(Document.batch_status == "processing").all()
        for doc in stuck:
            doc.batch_status = "summarized" if (doc.extraction and doc.extraction.summary_json) else "pending"
        stuck_reindex = db.query(Document).filter(Document.status == "processing").all()
        for doc in stuck_reindex:
            doc.status = "error"
        if stuck or stuck_reindex:
            db.commit()


def mark_all_interrupted():
    """Mark in-memory running jobs as interrupted on clean shutdown."""
    to_persist = []
    with _jobs_lock:
        for job in _active_jobs:
            if job.get("status") == "running":
                job["status"] = "interrupted"
                job["stage"] = "伺服器關閉中斷"
                to_persist.append(dict(job))
    for snap in to_persist:
        _persist_job(snap)


# ── Async workers ────────────────────────────────────────────────────────────────

async def do_delayed_remove(doc_id: int, delay: float = 0) -> None:
    pass  # Completed jobs stay as history


async def run_one_extraction(doc_id: int, on_failure: str):
    from services.extraction import extract_document_summary_with_raw
    jobs_set_running(doc_id)
    await push_jobs()
    job_error: str | None = None
    try:
        async def _stage_cb(label: str, _doc_id: int = doc_id):
            jobs_set_stage(_doc_id, label)
            await _push_jobs_payload(_jobs_payload())

        summary, observation_id, answer, sources = await extract_document_summary_with_raw(doc_id, on_stage=_stage_cb)
        await asyncio.to_thread(db_set_research_step1, doc_id, answer, sources, observation_id)
        await asyncio.to_thread(db_set_summarized, doc_id, summary)
    except asyncio.CancelledError:
        job_error = "已取消"
        await asyncio.to_thread(db_set_error, doc_id, job_error)
    except Exception as e:
        job_error = str(e)
        if on_failure == "restore_summarized":
            await asyncio.to_thread(db_restore_summarized, doc_id, job_error)
        else:
            await asyncio.to_thread(db_set_error, doc_id, job_error)
    finally:
        jobs_mark_done(doc_id, error=job_error)
        await push_jobs()
        asyncio.create_task(do_delayed_remove(doc_id))


async def run_one_extraction_step(doc_id: int, step: str, on_failure: str):
    from services.extraction import generate_interest_step3, run_document_research_step1, structure_research_step2
    jobs_set_running(doc_id)
    await push_jobs()
    job_error: str | None = None
    try:
        async def _stage_cb(label: str, _doc_id: int = doc_id):
            jobs_set_stage(_doc_id, label)
            await _push_jobs_payload(_jobs_payload())

        if step == "step1":
            answer, sources, observation_id = await run_document_research_step1(doc_id, on_stage=_stage_cb)
            if not answer or answer.strip() == "Unable to generate a response.":
                raise ValueError("Step 1 沒有產生可用研究摘要")
            await asyncio.to_thread(db_set_research_step1, doc_id, answer, sources, observation_id)
            return

        answer = await asyncio.to_thread(db_get_research_step1, doc_id)
        if not answer:
            raise ValueError("尚未保存 Step 1 原始研究摘要，請先跑 Step 1")

        if step == "step2":
            patch = await structure_research_step2(answer, on_stage=_stage_cb)
            await asyncio.to_thread(db_set_summary_patch, doc_id, patch)
        elif step == "step3":
            patch = await generate_interest_step3(answer, on_stage=_stage_cb)
            await asyncio.to_thread(db_set_summary_patch, doc_id, patch)
        else:
            raise ValueError(f"Unknown extraction step: {step}")
    except asyncio.CancelledError:
        job_error = "已取消"
        await asyncio.to_thread(db_set_error, doc_id, job_error)
    except Exception as e:
        job_error = str(e)
        if on_failure == "restore_summarized":
            await asyncio.to_thread(db_restore_summarized, doc_id, job_error)
        else:
            await asyncio.to_thread(db_set_error, doc_id, job_error)
    finally:
        jobs_mark_done(doc_id, error=job_error)
        await push_jobs()
        asyncio.create_task(do_delayed_remove(doc_id))


async def run_extraction_batch(pairs: list[tuple[int, str]], on_failure: str = "error"):
    if redis_enabled():
        for doc_id, _ in pairs:
            await redis_rpush(QUEUE_EXTRACT, {"doc_id": doc_id, "on_failure": on_failure, "step": "full"})
    else:
        async with _extract_condition:
            for doc_id, _ in pairs:
                _extract_work_queue.append({"doc_id": doc_id, "on_failure": on_failure, "step": "full"})
            _extract_condition.notify_all()


async def run_extraction_step_batch(
    pairs: list[tuple[int, str]],
    step: str,
    on_failure: str = "error",
):
    if redis_enabled():
        for doc_id, _ in pairs:
            await redis_rpush(QUEUE_EXTRACT, {"doc_id": doc_id, "on_failure": on_failure, "step": step})
    else:
        async with _extract_condition:
            for doc_id, _ in pairs:
                _extract_work_queue.append({"doc_id": doc_id, "on_failure": on_failure, "step": step})
            _extract_condition.notify_all()


async def enqueue_parse_jobs(items: list[dict]) -> int:
    """Queue parser-cache jobs. Each item needs doc_id, filename, file_path, parser, job_id."""
    if not items:
        return 0
    if redis_enabled():
        for item in items:
            await redis_rpush(QUEUE_PARSE, item)
    else:
        async with _parse_condition:
            for item in items:
                _parse_work_queue.append(item)
            _parse_condition.notify_all()
    await push_jobs()
    return len(items)


async def run_parse_job(doc_id: int, filename: str, raw_path: str, parser: str, job_id: str):
    from rag import parse_pdf_to_cache
    from services import storage_service

    file_path = raw_path
    tmp_path: str | None = None
    try:
        jobs_set_parse_running(job_id)
        await push_jobs()

        if storage_service._USE_BLOB:
            try:
                tmp_path = await asyncio.to_thread(storage_service.download_blob_to_tmp, raw_path)
                file_path = tmp_path
            except Exception as e:
                logger.error("Parse: blob download failed for doc %d: %s", doc_id, e)
                return
        elif not os.path.exists(file_path):
            logger.warning("Parse: file not found for doc %d: %s", doc_id, file_path)
            return

        await asyncio.to_thread(parse_pdf_to_cache, file_path, parser, doc_id)
    except Exception as e:
        logger.error("Parse failed for doc %d (%s): %s", doc_id, parser, e)
    finally:
        jobs_finalize_parse(job_id)
        await push_jobs()
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)


async def run_reindex_job(doc_id: int, file_path: str, tmp_path: str | None, parser: str = "auto"):
    from db import SessionLocal, Document  # kept manual: cross-await + multi-except cleanup
    from rag import process_pdf, delete_old_document_vectors, delete_pending_document_vectors
    jobs_set_running(doc_id)
    await push_jobs()
    s = SessionLocal()
    ts = int(time.time())
    succeeded = False
    reindex_error: str | None = None
    try:
        loop = asyncio.get_running_loop()

        def _stage_cb(label: str):
            jobs_set_stage(doc_id, label)
            asyncio.run_coroutine_threadsafe(_push_jobs_payload(_jobs_payload()), loop)

        chunks, abstract, quality_issue, parser_used = await asyncio.to_thread(
            process_pdf, file_path, doc_id, _stage_cb, ts, parser, True
        )
        succeeded = True

        jobs_set_stage(doc_id, "刪除舊向量")
        await push_jobs()
        await asyncio.to_thread(delete_old_document_vectors, doc_id, ts)

        doc = s.query(Document).filter(Document.id == doc_id).first()
        if doc:
            doc.status = "ready"
            doc.quality_issue = quality_issue
            doc.parser_used = parser_used
            doc.needs_reindex = False
            if not doc.abstract_edited:
                doc.abstract_text = abstract
            s.commit()
    except asyncio.CancelledError:
        reindex_error = "已取消"
        if not succeeded:
            await asyncio.to_thread(delete_pending_document_vectors, doc_id, ts)
        doc = s.query(Document).filter(Document.id == doc_id).first()
        if doc:
            doc.status = "error"
            s.commit()
        logger.info("Reindex cancelled for doc %d", doc_id)
    except Exception as e:
        reindex_error = str(e)
        if not succeeded:
            await asyncio.to_thread(delete_pending_document_vectors, doc_id, ts)
        doc = s.query(Document).filter(Document.id == doc_id).first()
        if doc:
            doc.status = "error"
            s.commit()
        logger.error("Reindex failed for doc %d: %s", doc_id, e)
    finally:
        s.close()
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)
        jobs_mark_done(doc_id, error=reindex_error if reindex_error else None)
        await push_jobs()
        asyncio.create_task(do_delayed_remove(doc_id))


async def _reindex_worker():
    while True:
        if redis_enabled():
            # Blocking pop from Redis — works across all instances
            item = await redis_blpop(QUEUE_REINDEX, timeout=5)
            if item is None:
                continue
        else:
            if not _reindex_work_queue:
                _reindex_event.clear()
                await _reindex_event.wait()
                continue
            item = _reindex_work_queue.pop(0)
        task = asyncio.create_task(
            run_reindex_job(item["doc_id"], item["file_path"], item["tmp_path"], item.get("parser", "auto"))
        )
        _reindex_tasks[item["doc_id"]] = task
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
        finally:
            _reindex_tasks.pop(item["doc_id"], None)


async def _extract_worker():
    while True:
        if redis_enabled():
            item = await redis_blpop(QUEUE_EXTRACT, timeout=5)
            if item is None:
                continue
        else:
            async with _extract_condition:
                while not _extract_work_queue:
                    await _extract_condition.wait()
                item = _extract_work_queue.pop(0)
        doc_id = item["doc_id"]
        step = item.get("step", "full")
        coro = run_one_extraction(doc_id, item["on_failure"]) if step == "full" \
            else run_one_extraction_step(doc_id, step, item["on_failure"])
        task = asyncio.create_task(coro)
        _extract_tasks[doc_id] = task
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
        finally:
            _extract_tasks.pop(doc_id, None)


async def _parse_worker():
    while True:
        if redis_enabled():
            item = await redis_blpop(QUEUE_PARSE, timeout=5)
            if item is None:
                continue
        else:
            async with _parse_condition:
                while not _parse_work_queue:
                    await _parse_condition.wait()
                item = _parse_work_queue.pop(0)
        await run_parse_job(
            item["doc_id"],
            item["filename"],
            item["file_path"],
            item["parser"],
            item["job_id"],
        )


async def _redis_cancel_listener() -> None:
    """Listen for cancel events published by other instances and cancel local tasks."""
    import redis.asyncio as aioredis
    r = get_redis()
    if not r:
        return
    try:
        async with r.pubsub() as pubsub:
            await pubsub.subscribe(EVENTS_CHANNEL)
            async for msg in pubsub.listen():
                if msg["type"] != "message":
                    continue
                try:
                    event = json.loads(msg["data"])
                    if event.get("type") == "cancel":
                        doc_id = int(event.get("doc_id", 0))
                        for tasks_dict in (_reindex_tasks, _extract_tasks):
                            task = tasks_dict.get(doc_id)
                            if task and not task.done():
                                task.cancel()
                                logger.info("redis_cancel_listener: cancelled local task for doc_id=%d", doc_id)
                except Exception as exc:
                    logger.debug("redis_cancel_listener parse error: %s", exc)
    except Exception as exc:
        logger.warning("redis_cancel_listener exited: %s", exc)


def init_workers():
    global _reindex_event, _extract_condition, _parse_condition
    _reindex_event = asyncio.Event()
    _extract_condition = asyncio.Condition()
    _parse_condition = asyncio.Condition()
    _worker_tasks.clear()
    _worker_tasks.append(asyncio.create_task(_reindex_worker(), name="reindex_worker"))
    _worker_tasks.append(asyncio.create_task(_parse_worker(), name="parse_worker"))
    _worker_tasks.append(asyncio.create_task(_extract_worker(), name="extract_worker_0"))
    _worker_tasks.append(asyncio.create_task(_extract_worker(), name="extract_worker_1"))
    if redis_enabled():
        _worker_tasks.append(asyncio.create_task(_redis_cancel_listener(), name="redis_cancel_listener"))


async def shutdown_workers() -> None:
    """Cancel and await all background worker tasks."""
    for task in _worker_tasks:
        if not task.done():
            task.cancel()
    if _worker_tasks:
        await asyncio.gather(*_worker_tasks, return_exceptions=True)
    _worker_tasks.clear()
