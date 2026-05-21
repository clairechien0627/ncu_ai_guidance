"""In-memory tracking for active chat stream jobs with cancel-signal support."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

_active: dict[str, dict] = {}          # thread_id → job info
_cancel: dict[str, asyncio.Event] = {} # thread_id → cancel event


def start(thread_id: str, message: str, user_id: str | None = None, title: str | None = None) -> None:
    _cancel.pop(thread_id, None)
    _active[thread_id] = {
        "thread_id": thread_id,
        "job_type": "chat",
        "status": "running",
        "title": title,
        "message_preview": message[:80],
        "user_id": user_id,
        "started_at": datetime.now(timezone.utc).isoformat(),
    }


def finish(thread_id: str) -> None:
    _active.pop(thread_id, None)
    _cancel.pop(thread_id, None)


def request_cancel(thread_id: str) -> bool:
    if thread_id not in _active:
        return False
    _active[thread_id]["status"] = "cancelling"
    ev = _cancel.get(thread_id)
    if ev is None:
        ev = asyncio.Event()
        _cancel[thread_id] = ev
    ev.set()
    return True


def is_cancelled(thread_id: str) -> bool:
    ev = _cancel.get(thread_id)
    return ev is not None and ev.is_set()


def get_active() -> list[dict]:
    return list(_active.values())
