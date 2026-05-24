"""DDL bootstrapping: create tables on first start.

Schema evolution (ADD COLUMN, CREATE INDEX, etc.) is fully managed by
Alembic versions in alembic/versions/.  This file only creates the base
table structure for brand-new databases; Alembic then brings them up to date.
"""
import logging as _logging

from .session import Base, engine

# Import all models so Base.metadata knows about every table.
from .models import (  # noqa: F401
    User,
    Document, DocumentExtraction, JobHistory, Conversation, PromptVersion,
    TraceEventOutbox, TraceV2, Observation, Score, ScoreConfig,
    EvaluationRun, EvaluationRunItem, Dataset, DatasetItem,
    ExperimentRun, ExperimentRunItem,
)

_logger = _logging.getLogger(__name__)


def create_tables() -> None:
    Base.metadata.create_all(bind=engine)
