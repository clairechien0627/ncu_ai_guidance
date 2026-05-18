"""Payload and legacy adapters for v2-first trace reads."""
from __future__ import annotations

from sqlalchemy.orm import Session

from services.trace_read_core import _TraceReadCore
from services.trace_read_common import Trace, legacy_trace_payload


class TraceLegacyAdapter:
    def __init__(self, core: _TraceReadCore):
        self.core = core

    def filtered(self, filters: dict, *, limit: int = 100000, offset: int = 0) -> list[Trace]:
        return self.core._legacy_filtered(filters, limit=limit, offset=offset)

    def payload(self, trace: Trace, *, include_raw: bool = False, include_display: bool = True) -> dict:
        return legacy_trace_payload(trace, include_raw=include_raw, include_display=include_display)

    def aggregate(self, traces: list[Trace]) -> dict:
        return self.core._legacy_agg(traces)

    def dimension_avgs(self, traces: list[Trace]) -> dict:
        return self.core._legacy_dimension_avgs(traces)


class TracePayloadReadService:
    def __init__(self, db: Session, core: _TraceReadCore | None = None):
        self.core = core or _TraceReadCore(db)
        self.legacy = TraceLegacyAdapter(self.core)

    def has_v2(self) -> bool:
        return self.core.has_v2()

    def environments(self) -> list[str]:
        return self.core.environments()

    def merged_payloads(self, filters: dict, *, include_raw: bool = False, include_display: bool = True, legacy_limit: int = 100000) -> list[dict]:
        return self.core._merged_payloads(filters, include_raw=include_raw, include_display=include_display, legacy_limit=legacy_limit)
