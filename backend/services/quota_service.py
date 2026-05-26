"""Per-user daily quota enforcement based on traces_v2 token usage."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from db.models import User, TraceV2, Observation


def _today_start() -> datetime:
    now = datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def check_quota(user: User, db: Session) -> None:
    """Raise HTTP 429 if the user has exceeded their daily quota."""
    user_id_str = user.public_id

    if user.quota_requests_per_day is not None:
        today_requests = (
            db.query(func.count(TraceV2.id))
            .filter(
                TraceV2.user_id == user_id_str,
                TraceV2.start_time >= _today_start(),
            )
            .scalar() or 0
        )
        if today_requests >= user.quota_requests_per_day:
            raise HTTPException(429, f"今日請求數已達上限（{user.quota_requests_per_day} 次）")

    if user.quota_tokens_per_day is not None:
        today_tokens = (
            db.query(func.sum(Observation.total_tokens))
            .join(TraceV2, Observation.trace_id == TraceV2.trace_id)
            .filter(
                TraceV2.user_id == user_id_str,
                TraceV2.start_time >= _today_start(),
            )
            .scalar() or 0
        )
        if today_tokens >= user.quota_tokens_per_day:
            raise HTTPException(429, f"今日 Token 用量已達上限（{user.quota_tokens_per_day}）")
