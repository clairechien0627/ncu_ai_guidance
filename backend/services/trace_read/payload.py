"""Payload helpers for Trace v2 reads."""
from __future__ import annotations

from sqlalchemy.orm import Session

from .core import _TraceReadCore


class TracePayloadReadService:
    def __init__(self, db: Session, core: _TraceReadCore | None = None):
        self.core = core or _TraceReadCore(db)

    def has_v2(self) -> bool:
        return self.core.has_v2()

    def environments(self) -> list[str]:
        return self.core.environments()

    def merged_payloads(self, filters: dict, *, include_raw: bool = False) -> list[dict]:
        return self.core._merged_payloads(filters, include_raw=include_raw)
