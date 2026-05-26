"""Small pure helpers for job state and persistence formatting."""

import datetime
import json
from uuid import uuid4


def restore_stage_log(row) -> list[str]:
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


def utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


def iso(dt: datetime.datetime | None = None) -> str:
    return (dt or utcnow()).isoformat() + "Z"


def dt_to_iso(dt: datetime.datetime | None) -> str | None:
    """Convert a DB datetime to a UTC ISO 'Z' string."""
    if dt is None:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(datetime.timezone.utc).replace(tzinfo=None)
    return dt.isoformat() + "Z"


def new_job_id(job_type: str, doc_id: int) -> str:
    return f"{job_type}:{doc_id}:{uuid4().hex[:10]}"


def is_active_status(status: str | None, terminal_statuses: set[str]) -> bool:
    return (status or "queued") not in terminal_statuses
