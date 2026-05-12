import asyncio
import json
import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from services import job_service

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/jobs")
def get_active_jobs():
    return job_service.get_jobs()


@router.get("/api/jobs/stream")
async def stream_jobs():
    queue: asyncio.Queue = asyncio.Queue(maxsize=20)
    job_service._job_subscribers.append(queue)

    async def generator():
        try:
            yield f"data: {json.dumps(job_service.get_jobs())}\n\n"
            while True:
                try:
                    data = await asyncio.wait_for(queue.get(), timeout=25)
                    yield f"data: {data}\n\n"
                except asyncio.TimeoutError:
                    yield ": heartbeat\n\n"
        finally:
            if queue in job_service._job_subscribers:
                job_service._job_subscribers.remove(queue)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/api/jobs/clear-history")
async def clear_job_history():
    before = len(job_service._active_jobs)
    job_service.jobs_clear_done()
    logger.warning("clear_job_history: %d → %d entries", before, len(job_service._active_jobs))
    await job_service.push_jobs()
    return {"ok": True}


@router.post("/api/jobs/reorder")
async def reorder_jobs(body: dict):
    doc_id = body.get("doc_id")
    new_index = int(body.get("index", 0))
    ok = await job_service.jobs_reorder_and_sync(doc_id, new_index)
    if not ok:
        raise HTTPException(status_code=404, detail="Job not found")
    await job_service.push_jobs()
    return {"ok": True}


@router.post("/api/jobs/{doc_id}/cancel")
async def cancel_reindex_job(doc_id: int):
    from database import get_db, Document, SessionLocal
    result = await job_service.jobs_cancel_queued(doc_id)
    if result == "not_found":
        # Not in queue — may be a stale processing state after restart
        s = SessionLocal()
        try:
            doc = s.query(Document).filter(Document.id == doc_id).first()
            if not doc:
                raise HTTPException(status_code=404, detail="Document not found")
            if doc.status == "processing":
                doc.status = "error"
                s.commit()
        finally:
            s.close()
    elif result == "queued":
        s = SessionLocal()
        try:
            doc = s.query(Document).filter(Document.id == doc_id).first()
            if doc:
                doc.status = "ready" if doc.status == "processing" else doc.status
                doc.batch_status = "pending" if doc.batch_status == "processing" else doc.batch_status
                s.commit()
        finally:
            s.close()
    return {"id": doc_id, "cancelled": True}
