"""Database package — session, models, and DDL bootstrapping.

New code should import directly from the sub-modules:
  from db.session import SessionLocal, get_db
  from db.models import Document, TraceV2
  from db.migrations import create_tables
"""
from .session import engine, SessionLocal, Base, get_db, db_session
from .models import (
    JsonColumn,
    User,
    Document,
    DocumentExtraction,
    DocumentResearchCache,
    JobHistory,
    Conversation,
    TraceEventOutbox,
    TraceV2,
    Observation,
    Score,
    ScoreConfig,
    EvaluationRun,
    EvaluationRunItem,
    Dataset,
    DatasetItem,
    ExperimentRun,
    ExperimentRunItem,
    PromptVersion,
)
from .migrations import create_tables

__all__ = [
    "engine",
    "SessionLocal",
    "Base",
    "get_db",
    "db_session",
    "JsonColumn",
    "User",
    "Document",
    "DocumentExtraction",
    "DocumentResearchCache",
    "JobHistory",
    "Conversation",
    "TraceEventOutbox",
    "TraceV2",
    "Observation",
    "Score",
    "ScoreConfig",
    "EvaluationRun",
    "EvaluationRunItem",
    "Dataset",
    "DatasetItem",
    "ExperimentRun",
    "ExperimentRunItem",
    "PromptVersion",
    "create_tables",
]
