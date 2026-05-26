from datetime import datetime

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db.models import Observation, TraceV2, User
from db.session import Base
from services.quota_service import check_quota


def _session_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)


def _user(**kwargs) -> User:
    return User(
        id=kwargs.pop("id", 1),
        username=kwargs.pop("username", "alice"),
        email=kwargs.pop("email", "alice@example.com"),
        hashed_pw=kwargs.pop("hashed_pw", "hash"),
        role=kwargs.pop("role", "user"),
        public_id=kwargs.pop("public_id", "public-user-1"),
        quota_requests_per_day=kwargs.pop("quota_requests_per_day", None),
        quota_tokens_per_day=kwargs.pop("quota_tokens_per_day", None),
        **kwargs,
    )


def _trace(trace_id: str, user_id: str) -> TraceV2:
    return TraceV2(
        trace_id=trace_id,
        name="router_agent",
        user_id=user_id,
        environment="test",
        start_time=datetime.utcnow(),
    )


def test_request_quota_uses_public_id_not_numeric_id():
    SessionLocal = _session_factory()
    db = SessionLocal()
    try:
        user = _user(id=123, public_id="public-user-1", quota_requests_per_day=1)
        db.add(_trace("numeric-old-trace", "123"))
        db.commit()

        check_quota(user, db)

        db.add(_trace("public-current-trace", "public-user-1"))
        db.commit()

        with pytest.raises(HTTPException) as exc:
            check_quota(user, db)
        assert exc.value.status_code == 429
    finally:
        db.close()


def test_token_quota_uses_public_id_not_numeric_id():
    SessionLocal = _session_factory()
    db = SessionLocal()
    try:
        user = _user(id=123, public_id="public-user-1", quota_tokens_per_day=10)
        db.add(_trace("public-trace", "public-user-1"))
        db.add(Observation(
            observation_id="obs-1",
            trace_id="public-trace",
            type="GENERATION",
            name="LLM",
            environment="test",
            total_tokens=10,
            start_time=datetime.utcnow(),
        ))
        db.commit()

        with pytest.raises(HTTPException) as exc:
            check_quota(user, db)
        assert exc.value.status_code == 429
    finally:
        db.close()
