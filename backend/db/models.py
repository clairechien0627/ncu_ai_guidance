"""ORM model definitions and JsonColumn type adapter."""
import json as _json
import logging as _logging
from datetime import datetime, timezone

from sqlalchemy import (
    Column, Integer, String, DateTime, Text, Boolean, Float, ForeignKey,
    TypeDecorator, UniqueConstraint,
)
from sqlalchemy.orm import relationship

from .session import Base

_db_logger = _logging.getLogger(__name__)


class JsonColumn(TypeDecorator):
    """JSONB on PostgreSQL, Text elsewhere.

    Python interface is always a JSON string so existing json.loads/json.dumps
    call sites need no changes. The column stores as JSONB internally on
    PostgreSQL, enabling GIN indexes and ->> path queries.
    """
    impl = Text
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            from sqlalchemy.dialects.postgresql import JSONB
            return dialect.type_descriptor(JSONB())
        return dialect.type_descriptor(Text())

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if dialect.name == "postgresql":
            # JSONB storage expects a Python object, not a string.
            if isinstance(value, str):
                stripped = value.strip()
                if not stripped:
                    return None
                try:
                    return _json.loads(stripped)
                except Exception:
                    _db_logger.warning("JsonColumn: invalid JSON will be stored as NULL: %r", stripped[:200])
                    return None
            return value
        # Non-PostgreSQL (e.g. SQLite in tests): store as string.
        if isinstance(value, (dict, list)):
            return _json.dumps(value, ensure_ascii=False)
        return value

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if dialect.name == "postgresql":
            # JSONB returns a Python object; convert back to string for compat.
            if isinstance(value, str):
                return value
            try:
                return _json.dumps(value, ensure_ascii=False)
            except Exception:
                _db_logger.warning("JsonColumn: non-serialisable JSONB value, falling back to str(): %r", repr(value)[:100])
                return str(value)
        return value


class Document(Base):
    __tablename__ = "documents"
    id = Column(Integer, primary_key=True, index=True)
    filename = Column(String, nullable=False)
    file_path = Column(String, nullable=False)
    status = Column(String, default="processing")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    # Pipeline state (stays here — queried directly without JOIN)
    batch_status = Column(String, default="pending")   # pending/classified/summarized/embedded/error
    error_message = Column(Text, nullable=True)
    # DEPRECATED: new writes go to DocumentExtraction; kept for backward compat.
    # TODO: remove once all read paths migrate to DocumentExtraction.
    summary_json = Column(JsonColumn, nullable=True)          # DEPRECATED
    category = Column(String, nullable=True)                  # DEPRECATED
    tags = Column(JsonColumn, nullable=True)                  # DEPRECATED
    department_hint = Column(String, nullable=True)           # DEPRECATED
    langsmith_run_id = Column(String, nullable=True)          # DEPRECATED
    raw_research_answer = Column(Text, nullable=True)         # DEPRECATED
    raw_research_sources = Column(JsonColumn, nullable=True)  # DEPRECATED
    raw_research_run_id = Column(String, nullable=True)       # DEPRECATED
    # Document metadata
    abstract_text = Column(Text, nullable=True)
    abstract_edited = Column(Boolean, default=False)
    quality_issue = Column(String, nullable=True)
    parser_used = Column(String, nullable=True)
    file_hash = Column(String, nullable=True, index=True)
    needs_reindex = Column(Boolean, default=False)
    deleted_at = Column(DateTime, nullable=True)

    # order_by uses a string ref here because DocumentExtraction is defined below;
    # SQLAlchemy resolves it at mapper configuration time.
    extraction = relationship(
        "DocumentExtraction",
        back_populates="document",
        uselist=False,
        primaryjoin="Document.id == foreign(DocumentExtraction.document_id)",
        order_by="DocumentExtraction.version.desc()",
    )

    def __repr__(self) -> str:
        return f"<Document id={self.id} filename={self.filename!r} status={self.status!r}>"


class DocumentExtraction(Base):
    """Extraction results for a document, separated from core document metadata.

    Supports future multi-version results (version field). Currently 1:1 with Document.
    All new writes go here; the deprecated columns on Document are kept for backward compat.
    """
    __tablename__ = "document_extractions"
    id = Column(Integer, primary_key=True, index=True)
    document_id = Column(Integer, ForeignKey("documents.id"), nullable=False, index=True)
    version = Column(Integer, default=1, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))

    summary_json = Column(JsonColumn, nullable=True)
    tags = Column(JsonColumn, nullable=True)
    category = Column(String, nullable=True)
    department_hint = Column(String, nullable=True)
    raw_research_answer = Column(Text, nullable=True)
    raw_research_sources = Column(JsonColumn, nullable=True)
    raw_research_run_id = Column(String, nullable=True)
    langsmith_run_id = Column(String, nullable=True)

    document = relationship("Document", back_populates="extraction")


class JobHistory(Base):
    __tablename__ = "job_history"
    id = Column(Integer, primary_key=True, index=True)
    job_id = Column(String, nullable=True, index=True)
    doc_id = Column(Integer, nullable=False, index=True)
    filename = Column(String, nullable=False)
    job_type = Column(String, nullable=False)   # 'reindex' | 'extract'
    status = Column(String, default="done", index=True)  # queued/running/done/error/cancelled
    stage = Column(Text, nullable=True)
    stage_log = Column(Text, nullable=True)   # JSON array of stage strings
    error = Column(Text, nullable=True)
    started_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class DocumentResearchCache(Base):
    """Per-document, per-coverage-template research cache.

    Stores the complete coverage result from a previous research run so subsequent
    runs on the same document can pre-load evidence and skip already-filled slots.
    Keyed by (document_id, coverage_hash) where coverage_hash is the MD5 of the
    sorted coverage_item ids used in that run.
    """
    __tablename__ = "document_research_cache"
    __table_args__ = (UniqueConstraint("document_id", "coverage_hash", name="uq_doc_coverage"),)

    id = Column(Integer, primary_key=True, index=True)
    document_id = Column(Integer, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True)
    coverage_hash = Column(String(32), nullable=False)
    slot_status = Column(JsonColumn, nullable=True)
    evidence = Column(JsonColumn, nullable=True)
    evidence_details = Column(JsonColumn, nullable=True)
    known_keywords = Column(JsonColumn, nullable=True)
    avoid_query_terms = Column(JsonColumn, nullable=True)
    sources = Column(JsonColumn, nullable=True)
    search_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))


class Conversation(Base):
    __tablename__ = "conversations"
    id = Column(Integer, primary_key=True, index=True)
    model = Column(String, default="openai")
    title = Column(String, nullable=True)
    message_count = Column(Integer, default=0)
    context_summary = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class Trace(Base):
    __tablename__ = "traces"
    id = Column(Integer, primary_key=True, index=True)
    run_id = Column(String, unique=True, index=True, nullable=False)
    parent_run_id = Column(String, nullable=True, index=True)
    run_type = Column(String, nullable=False)          # 'llm' | 'tool' | 'chain'
    name = Column(String, nullable=False)
    inputs = Column(JsonColumn, nullable=True)          # JSON
    outputs = Column(JsonColumn, nullable=True)         # JSON
    error = Column(Text, nullable=True)
    start_time = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    end_time = Column(DateTime, nullable=True)
    prompt_tokens = Column(Integer, nullable=True)
    completion_tokens = Column(Integer, nullable=True)
    thread_id = Column(String, nullable=True, index=True)
    document_ids = Column(JsonColumn, nullable=True)    # JSON array, e.g. "[1,2]"
    task_type = Column(String, nullable=True, index=True)
    route_intent = Column(String, nullable=True, index=True)
    agent_name = Column(String, nullable=True, index=True)
    prompt_name = Column(String, nullable=True, index=True)
    prompt_version = Column(String, nullable=True)
    base_prompt_name = Column(String, nullable=True)
    task_prompt_name = Column(String, nullable=True)
    quality_prompt_name = Column(String, nullable=True)
    base_prompt_hash = Column(String, nullable=True)
    task_prompt_hash = Column(String, nullable=True)
    quality_prompt_hash = Column(String, nullable=True)
    prompt_stack_name = Column(String, nullable=True)
    prompt_stack_json = Column(JsonColumn, nullable=True)
    primary_prompt_json = Column(JsonColumn, nullable=True)
    workflow_prompts_json = Column(JsonColumn, nullable=True)
    prompt_stack_tokens = Column(Integer, nullable=True)
    tool_count = Column(Integer, nullable=True)
    llm_call_count = Column(Integer, nullable=True)
    quality_score = Column(Float, nullable=True)
    user_feedback = Column(Text, nullable=True)
    display = Column(JsonColumn, nullable=True)
    original_intent = Column(String, nullable=True, index=True)
    resolved_intent = Column(String, nullable=True, index=True)
    quality_detail = Column(JsonColumn, nullable=True)

    def __repr__(self) -> str:
        return f"<Trace run_id={self.run_id!r} task_type={self.task_type!r} agent={self.agent_name!r}>"
