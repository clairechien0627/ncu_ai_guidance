"""In-memory store for mid-run steering messages.

One pending guidance string per thread_id. Consumed once at the next
orchestrator handoff decision and then cleared.
"""
from __future__ import annotations

_pending: dict[str, str] = {}


def set(thread_id: str, text: str) -> None:
    _pending[thread_id] = text


def get_and_clear(thread_id: str) -> str | None:
    return _pending.pop(thread_id, None)
