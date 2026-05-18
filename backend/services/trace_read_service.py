"""Compatibility facade for Trace Monitor read APIs.

Public API callers still use ``TraceReadService(db).method(...)``. The actual
read responsibilities are split across list/detail/stats/session services.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from services.trace_read_core import _TraceReadCore
from services.trace_read_detail import TraceDetailReadService
from services.trace_read_list import TraceListReadService
from services.trace_read_payload import TraceLegacyAdapter, TracePayloadReadService
from services.trace_read_sessions import TraceSessionUserReadService
from services.trace_read_stats import TraceStatsReadService


class TraceReadService:
    def __init__(self, db: Session):
        self.db = db
        self._core = _TraceReadCore(db)
        self.legacy = TraceLegacyAdapter(self._core)
        self.payloads = TracePayloadReadService(db, self._core)
        self._list = TraceListReadService(self._core)
        self._detail = TraceDetailReadService(self._core)
        self._stats = TraceStatsReadService(self._core)
        self._sessions = TraceSessionUserReadService(self._core)

    def has_v2(self) -> bool:
        return self.payloads.has_v2()

    def environments(self) -> list[str]:
        return self.payloads.environments()

    def list_traces(self, *, limit: int = 40, offset: int = 0, **filters) -> list[dict]:
        return self._list.list_traces(limit=limit, offset=offset, **filters)

    def document_traces(self, doc_id: int, *, limit: int = 10) -> list[dict]:
        return self._list.document_traces(doc_id, limit=limit)

    def errors(self, *, limit: int = 40) -> list[dict]:
        return self._list.errors(limit=limit)

    def slow_runs(self, *, limit: int = 40, min_latency: float = 10) -> list[dict]:
        return self._list.slow_runs(limit=limit, min_latency=min_latency)

    def compare_prompt_versions(self, v1: str, v2: str, *, limit: int = 200) -> dict:
        return self._list.compare_prompt_versions(v1, v2, limit=limit)

    def trace_detail(self, run_id: str) -> dict:
        return self._detail.trace_detail(run_id)

    def stats(self, *, days: int = 7) -> dict:
        return self._stats.stats(days=days)

    def grouped(self, field: str) -> list[dict]:
        return self._stats.grouped(field)

    def observations(self, *, limit: int = 100, offset: int = 0, run_type: str | None = None) -> list[dict]:
        return self._stats.observations(limit=limit, offset=offset, run_type=run_type)

    def observation_stats(self) -> dict:
        return self._stats.observation_stats()

    def score_stats(self) -> dict:
        return self._stats.score_stats()

    def timeline(self, *, prompt_name: str | None = None, task_type: str | None = None, route_intent: str | None = None, days: int = 14) -> list[dict]:
        return self._stats.timeline(prompt_name=prompt_name, task_type=task_type, route_intent=route_intent, days=days)

    def sessions(self, *, limit: int = 50, offset: int = 0, route_intent: str | None = None, user_id: str | None = None, environment: str | None = None, date_from: str | None = None, date_to: str | None = None, order_by: str = "created_at", order_dir: str = "desc") -> dict:
        return self._sessions.sessions(limit=limit, offset=offset, route_intent=route_intent, user_id=user_id, environment=environment, date_from=date_from, date_to=date_to, order_by=order_by, order_dir=order_dir)

    def session_detail(self, thread_id: str) -> dict:
        return self._sessions.session_detail(thread_id)

    def users(self, *, limit: int = 50, offset: int = 0, environment: str | None = None, date_from: str | None = None, date_to: str | None = None, search: str | None = None) -> dict:
        return self._sessions.users(limit=limit, offset=offset, environment=environment, date_from=date_from, date_to=date_to, search=search)

    def user_detail(self, user_id: str) -> dict:
        return self._sessions.user_detail(user_id)
