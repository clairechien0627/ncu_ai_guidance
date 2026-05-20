"""Trace detail read operations."""
from __future__ import annotations

from .common import *  # noqa: F401,F403
from .core import _TraceReadCore
from services.trace_repositories import observation_to_trace_payload


class TraceDetailReadService:
    def __init__(self, core: _TraceReadCore):
        self.core = core

    def trace_detail(self, trace_id: str) -> dict:
        trace_v2 = self.core.db.query(TraceV2).filter(TraceV2.trace_id == trace_id).first()
        legacy = self.core.db.query(Trace).filter(Trace.observation_id == trace_id).first()
        if not trace_v2 and not legacy:
            raise HTTPException(status_code=404, detail="Trace not found")

        payload = _v2_trace_payload(self.core.db, trace_v2, include_raw=True) if trace_v2 else legacy_trace_payload(legacy, include_raw=True)
        children = (
            self.core.db.query(Observation)
            .filter(Observation.trace_id == trace_id)
            .order_by(Observation.start_time.asc())
            .limit(200)
            .all()
        )
        if children:
            payload["children"] = [observation_to_trace_payload(child) for child in children]
        else:
            legacy_children = (
                self.core.db.query(Trace)
                .filter(Trace.trace_id == trace_id)
                .order_by(Trace.start_time.asc())
                .limit(200)
                .all()
            )
            payload["children"] = [legacy_trace_payload(child, include_raw=True) for child in legacy_children]
        return payload

