"""Database package — session, models, and DDL bootstrapping.

New code should import directly from the sub-modules:
  from db.session import SessionLocal, get_db
  from db.models import Document, Trace
  from db.migrations import create_tables
"""
from .session import engine, SessionLocal, Base, get_db, db_session
from .models import (
    JsonColumn,
    Document,
    DocumentExtraction,
    DocumentResearchCache,
    JobHistory,
    Conversation,
    Trace,
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
)
from .migrations import create_tables

__all__ = [
    "engine",
    "SessionLocal",
    "Base",
    "get_db",
    "db_session",
    "JsonColumn",
    "Document",
    "DocumentExtraction",
    "DocumentResearchCache",
    "JobHistory",
    "Conversation",
    "Trace",
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
    "create_tables",
]
