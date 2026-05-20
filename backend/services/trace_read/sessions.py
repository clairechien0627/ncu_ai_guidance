"""Session and user trace read operations."""
from __future__ import annotations

from .common import *  # noqa: F401,F403
from .core import _TraceReadCore


class TraceSessionUserReadService:
    def __init__(self, core: _TraceReadCore):
        self.core = core

    def sessions(self, *, limit: int = 50, offset: int = 0, user_id: str | None = None,
                 environment: str | None = None, date_from: str | None = None, date_to: str | None = None,
                 order_by: str = "created_at", order_dir: str = "desc") -> dict:
        payloads = [
            p for p in self.core._merged_payloads(
                {"user_id": user_id, "environment": environment, "date_from": date_from, "date_to": date_to},
                include_raw=True,
                include_display=False,
                legacy_limit=2000,
            )
            if p.get("thread_id")
        ]
        result = self.core._sessions_from_payloads(payloads)
        self.core._sort_session_like(result, order_by, order_dir)
        return {"sessions": result[offset: offset + limit], "total": len(result)}


    def session_detail(self, thread_id: str) -> dict:
        payloads = [
            p for p in self.core._merged_payloads({}, include_raw=True, include_display=False)
            if p.get("thread_id") == thread_id
        ]
        if not payloads:
            raise HTTPException(status_code=404, detail="Session not found")
        def _ts(p):
            st = p.get("start_time")
            if not st:
                return datetime.min
            try:
                return datetime.fromisoformat(st.replace("Z", "+00:00")).replace(tzinfo=None)
            except Exception:
                return datetime.min
        payloads.sort(key=_ts)
        return self.core._session_detail_from_payloads(thread_id, payloads)


    def users(self, *, limit: int = 50, offset: int = 0, environment: str | None = None, date_from: str | None = None,
              date_to: str | None = None, search: str | None = None) -> dict:
        payloads = [
            p for p in self.core._merged_payloads(
                {"environment": environment, "date_from": date_from, "date_to": date_to},
                include_raw=True,
                include_display=False,
                legacy_limit=5000,
            )
            if self.core._payload_user_id(p)
        ]
        if search:
            payloads = [p for p in payloads if search.lower() in str(self.core._payload_user_id(p)).lower()]
        result = self.core._users_from_payloads(payloads)
        result.sort(key=lambda x: x["last_event"] or "", reverse=True)
        return {"users": result[offset: offset + limit], "total": len(result)}


    def user_detail(self, user_id: str) -> dict:
        payloads = self.core._merged_payloads({"user_id": user_id}, include_raw=True, include_display=False, legacy_limit=500)
        if not payloads:
            raise HTTPException(status_code=404, detail="User not found")
        sessions = self.core._sessions_from_payloads(payloads)
        return {
            "user_id": user_id,
            "first_event": min((p["start_time"] for p in payloads if p.get("start_time")), default=None),
            "last_event": max((p["start_time"] for p in payloads if p.get("start_time")), default=None),
            "session_count": len(sessions),
            "trace_count": len(payloads),
            "total_tokens": sum((p.get("prompt_tokens") or 0) + (p.get("completion_tokens") or 0) for p in payloads),
            "avg_quality_score": self.core._avg([p.get("quality_score") for p in payloads]),
            "sessions": sessions,
        }

