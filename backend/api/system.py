"""System management and observability endpoints for the admin Playground."""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from api.dependencies import require_admin
from db import get_db
from db.models import TraceEventOutbox, EvaluationRun, EvaluationRunItem

router = APIRouter(dependencies=[Depends(require_admin)])
logger = logging.getLogger(__name__)


@router.get("/api/system/queue-status")
def get_queue_status(db: Session = Depends(get_db)) -> dict[str, Any]:
    """Return outbox and evaluation worker queue depths."""
    from sqlalchemy import func

    # Trace ingestion outbox
    outbox_rows = (
        db.query(TraceEventOutbox.status, func.count())
        .group_by(TraceEventOutbox.status)
        .all()
    )
    outbox = {status: count for status, count in outbox_rows}

    # Evaluation run items
    eval_rows = (
        db.query(EvaluationRunItem.status, func.count())
        .group_by(EvaluationRunItem.status)
        .all()
    )
    eval_items = {status: count for status, count in eval_rows}

    # Active eval runs
    active_runs = (
        db.query(func.count())
        .filter(EvaluationRun.status.in_(["pending", "running", "partial"]))
        .scalar()
    )

    # Evaluation worker status
    try:
        from services.evaluation.worker import _worker_task
        worker_running = _worker_task is not None and not _worker_task.done()
    except Exception:
        worker_running = None

    return {
        "outbox": {
            "pending": outbox.get("pending", 0),
            "processing": outbox.get("processing", 0),
            "failed": outbox.get("failed", 0),
            "processed": outbox.get("processed", 0),
        },
        "evaluation": {
            "active_runs": active_runs or 0,
            "items_pending": eval_items.get("pending", 0),
            "items_running": eval_items.get("running", 0),
            "items_failed": eval_items.get("failed", 0),
            "items_completed": eval_items.get("completed", 0),
            "worker_running": worker_running,
        },
    }

