import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db import Score, ScoreConfig
from db.session import Base
from services.trace_repositories import ScoreRepository


def _session_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)


def test_default_score_configs_are_seeded_and_validate_numeric_range():
    SessionLocal = _session_factory()
    db = SessionLocal()
    try:
        ScoreRepository.seed_default_configs(db)
        db.commit()

        overall = db.query(ScoreConfig).filter(ScoreConfig.name == "overall").one()
        assert overall.data_type == "NUMERIC"
        assert overall.min_value == 0
        assert overall.max_value == 5

        try:
            ScoreRepository.upsert_score(db, {
                "score_id": "bad-overall",
                "trace_id": "trace-1",
                "name": "overall",
                "value": 6.0,
                "data_type": "NUMERIC",
            })
        except ValueError as exc:
            assert "above maximum" in str(exc)
        else:
            raise AssertionError("expected ValueError")
    finally:
        db.close()


def test_score_config_validation_accepts_categorical_and_text_scores():
    SessionLocal = _session_factory()
    db = SessionLocal()
    try:
        ScoreRepository.seed_default_configs(db)
        ScoreRepository.upsert_score(db, {
            "score_id": "verdict-1",
            "trace_id": "trace-1",
            "name": "verdict",
            "string_value": "可用",
            "data_type": "CATEGORICAL",
            "metadata": {"source": "test"},
        })
        ScoreRepository.upsert_score(db, {
            "score_id": "feedback-1",
            "trace_id": "trace-1",
            "name": "feedback",
            "string_value": "looks good",
            "data_type": "TEXT",
            "metadata": {"source": "test"},
        })
        db.commit()

        rows = db.query(Score).order_by(Score.score_id.asc()).all()
        assert [row.score_id for row in rows] == ["feedback-1", "verdict-1"]
        assert json.loads(rows[0].metadata_json)["source"] == "test"
    finally:
        db.close()


def test_categorical_score_rejects_unknown_option():
    SessionLocal = _session_factory()
    db = SessionLocal()
    try:
        ScoreRepository.seed_default_configs(db)

        try:
            ScoreRepository.upsert_score(db, {
                "score_id": "verdict-bad",
                "trace_id": "trace-1",
                "name": "verdict",
                "string_value": "unknown",
                "data_type": "CATEGORICAL",
            })
        except ValueError as exc:
            assert "one of" in str(exc)
        else:
            raise AssertionError("expected ValueError")
    finally:
        db.close()
