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
    department_hint = Column(String, nullable=True)
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
    research_observation_id = Column(String, nullable=True)

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
    """Per-document-set, per-coverage-template research cache.

    Keyed by (document_set_hash, coverage_hash):
    - document_set_hash: MD5 of sorted comma-joined document IDs — works for any
      number of documents, including single-doc runs.
    - coverage_hash: MD5 of sorted coverage_item IDs.

    document_id is kept for single-doc rows only (enables ON DELETE CASCADE);
    multi-doc rows set document_id=NULL.
    """
    __tablename__ = "document_research_cache"
    __table_args__ = (UniqueConstraint("document_set_hash", "coverage_hash", name="uq_docset_coverage"),)

    id = Column(Integer, primary_key=True, index=True)
    document_set_hash = Column(String(32), nullable=False, index=True)
    document_id = Column(Integer, ForeignKey("documents.id", ondelete="CASCADE"), nullable=True, index=True)
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


def _utcnow():
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"
    id         = Column(Integer, primary_key=True, index=True)
    email      = Column(String, unique=True, nullable=False, index=True)
    username   = Column(String, unique=True, nullable=False, index=True)
    hashed_pw  = Column(String, nullable=False)
    role       = Column(String, nullable=False, default="user")   # "user" | "admin"
    is_active  = Column(Boolean, nullable=False, default=True)
    display_name = Column(String, nullable=True)
    # 用量配額（None = 無限）
    quota_tokens_per_day   = Column(Integer, nullable=True)
    quota_requests_per_day = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)


class Conversation(Base):
    __tablename__ = "conversations"
    id = Column(Integer, primary_key=True, index=True)
    thread_id = Column(String(36), unique=True, nullable=True, index=True)
    user_id = Column(String, nullable=True, index=True)
    model = Column(String, default="openai")
    title = Column(String, nullable=True)
    message_count = Column(Integer, default=0)
    context_summary = Column(Text, nullable=True)
    last_agent_name = Column(String, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class Trace(Base):
    __tablename__ = "traces"
    id = Column(Integer, primary_key=True, index=True)
    observation_id = Column(String, unique=True, index=True, nullable=False)
    trace_id = Column(String, nullable=True, index=True)
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
    quality_detail = Column(JsonColumn, nullable=True)
    environment = Column(String(40), nullable=False, default="default", index=True)
    user_id = Column(String, nullable=True, index=True)

    def __repr__(self) -> str:
        return f"<Trace observation_id={self.observation_id!r} task_type={self.task_type!r} agent={self.agent_name!r}>"


class TraceEventOutbox(Base):
    __tablename__ = "trace_events_outbox"

    id = Column(Integer, primary_key=True, index=True)
    event_id = Column(String, unique=True, nullable=False, index=True)
    event_type = Column(String, nullable=False, index=True)
    body_json = Column(JsonColumn, nullable=False)
    status = Column(String(20), nullable=False, default="pending", index=True)
    attempts = Column(Integer, nullable=False, default=0)
    last_error = Column(Text, nullable=True)
    locked_at = Column(DateTime, nullable=True, index=True)
    locked_by = Column(String, nullable=True, index=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    processed_at = Column(DateTime, nullable=True)


class TraceV2(Base):
    __tablename__ = "traces_v2"

    id = Column(Integer, primary_key=True, index=True)
    trace_id = Column(String, unique=True, nullable=False, index=True)
    name = Column(String, nullable=False)
    thread_id = Column(String, nullable=True, index=True)
    user_id = Column(String, nullable=True, index=True)
    environment = Column(String(40), nullable=False, default="default", index=True)
    input = Column(JsonColumn, nullable=True)
    output = Column(JsonColumn, nullable=True)
    metadata_json = Column("metadata", JsonColumn, nullable=True)
    tags = Column(JsonColumn, nullable=True)
    start_time = Column(DateTime, nullable=False, index=True)
    end_time = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))

    def __repr__(self) -> str:
        return f"<TraceV2 trace_id={self.trace_id!r} name={self.name!r}>"


class Observation(Base):
    __tablename__ = "observations"

    id = Column(Integer, primary_key=True, index=True)
    observation_id = Column(String, unique=True, nullable=False, index=True)
    trace_id = Column(String, nullable=False, index=True)
    thread_id = Column(String, nullable=True, index=True)
    parent_observation_id = Column(String, nullable=True, index=True)
    type = Column(String, nullable=False, index=True)
    name = Column(String, nullable=False)
    model = Column(String, nullable=True, index=True)
    model_parameters = Column(JsonColumn, nullable=True)
    usage = Column(JsonColumn, nullable=True)
    cost = Column(JsonColumn, nullable=True)
    prompt_tokens = Column(Integer, nullable=True, index=True)
    completion_tokens = Column(Integer, nullable=True, index=True)
    total_tokens = Column(Integer, nullable=True, index=True)
    input_cost = Column(Float, nullable=True)
    output_cost = Column(Float, nullable=True)
    total_cost = Column(Float, nullable=True, index=True)
    prompt_name = Column(String, nullable=True, index=True)
    prompt_version = Column(String, nullable=True)
    input = Column(JsonColumn, nullable=True)
    output = Column(JsonColumn, nullable=True)
    metadata_json = Column("metadata", JsonColumn, nullable=True)
    status = Column(String(20), nullable=True, index=True)
    level = Column(String(20), nullable=False, default="DEFAULT", index=True)
    status_message = Column(Text, nullable=True)
    start_time = Column(DateTime, nullable=False, index=True)
    completion_start_time = Column(DateTime, nullable=True, index=True)
    end_time = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))

    def __repr__(self) -> str:
        return f"<Observation observation_id={self.observation_id!r} trace_id={self.trace_id!r}>"


class ScoreConfig(Base):
    __tablename__ = "score_configs"
    __table_args__ = (UniqueConstraint("name", name="uq_score_configs_name"),)

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False, index=True)
    data_type = Column(String, nullable=False, default="NUMERIC", index=True)
    min_value = Column(Float, nullable=True)
    max_value = Column(Float, nullable=True)
    categories = Column(JsonColumn, nullable=True)
    description = Column(Text, nullable=True)
    is_archived = Column(Boolean, nullable=False, default=False, index=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))


class Score(Base):
    __tablename__ = "scores"
    __table_args__ = (UniqueConstraint("score_id", name="uq_scores_score_id"),)

    id = Column(Integer, primary_key=True, index=True)
    score_id = Column(String, nullable=False, index=True)
    trace_id = Column(String, nullable=True, index=True)
    observation_id = Column(String, nullable=True, index=True)
    name = Column(String, nullable=False, index=True)
    value = Column(Float, nullable=True, index=True)
    string_value = Column(Text, nullable=True)
    data_type = Column(String, nullable=False, default="NUMERIC", index=True)
    source = Column(String, nullable=False, default="API", index=True)
    comment = Column(Text, nullable=True)
    metadata_json = Column("metadata", JsonColumn, nullable=True)
    execution_trace_id = Column(String, nullable=True, index=True)
    eval_run_id = Column(String, nullable=True, index=True)
    eval_item_id = Column(String, nullable=True, index=True)
    dataset_id = Column(String, nullable=True, index=True)
    dataset_item_id = Column(String, nullable=True, index=True)
    experiment_run_id = Column(String, nullable=True, index=True)
    experiment_item_id = Column(String, nullable=True, index=True)
    score_config_id = Column(Integer, ForeignKey("score_configs.id"), nullable=True, index=True)
    timestamp = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))

    def __repr__(self) -> str:
        return f"<Score score_id={self.score_id!r} name={self.name!r} trace_id={self.trace_id!r}>"


class EvaluationRun(Base):
    __tablename__ = "evaluation_runs"

    id = Column(Integer, primary_key=True, index=True)
    eval_run_id = Column(String, unique=True, nullable=False, index=True)
    name = Column(String, nullable=False, index=True)
    status = Column(String(20), nullable=False, default="pending", index=True)
    scope = Column(String(40), nullable=False, index=True)
    target_trace_ids = Column(JsonColumn, nullable=True)
    model = Column(String, nullable=True)
    prompt_name = Column(String, nullable=True, index=True)
    prompt_version = Column(String, nullable=True)
    dataset_id = Column(String, nullable=True, index=True)
    dataset_item_count = Column(Integer, nullable=False, default=0)
    metadata_json = Column("metadata", JsonColumn, nullable=True)
    total_count = Column(Integer, nullable=False, default=0)
    succeeded_count = Column(Integer, nullable=False, default=0)
    failed_count = Column(Integer, nullable=False, default=0)
    last_error = Column(Text, nullable=True)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))

    items = relationship("EvaluationRunItem", back_populates="run", cascade="all, delete-orphan")


class EvaluationRunItem(Base):
    __tablename__ = "evaluation_run_items"
    __table_args__ = (UniqueConstraint("eval_run_id", "trace_id", name="uq_eval_run_items_run_trace"),)

    id = Column(Integer, primary_key=True, index=True)
    eval_item_id = Column(String, unique=True, nullable=False, index=True)
    eval_run_id = Column(String, ForeignKey("evaluation_runs.eval_run_id", ondelete="CASCADE"), nullable=False, index=True)
    trace_id = Column(String, nullable=True, index=True)
    dataset_item_id = Column(String, nullable=True, index=True)
    experiment_item_id = Column(String, nullable=True, index=True)
    status = Column(String(20), nullable=False, default="pending", index=True)
    score_ids = Column(JsonColumn, nullable=True)
    error = Column(Text, nullable=True)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))

    run = relationship("EvaluationRun", back_populates="items")


class Dataset(Base):
    __tablename__ = "datasets"

    id = Column(Integer, primary_key=True, index=True)
    dataset_id = Column(String, unique=True, nullable=False, index=True)
    name = Column(String, nullable=False, index=True)
    description = Column(Text, nullable=True)
    source = Column(String, nullable=True, index=True)
    metadata_json = Column("metadata", JsonColumn, nullable=True)
    is_archived = Column(Boolean, nullable=False, default=False, index=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))

    items = relationship("DatasetItem", back_populates="dataset", cascade="all, delete-orphan")


class DatasetItem(Base):
    __tablename__ = "dataset_items"
    __table_args__ = (UniqueConstraint("dataset_id", "source_trace_id", name="uq_dataset_items_dataset_trace"),)

    id = Column(Integer, primary_key=True, index=True)
    dataset_item_id = Column(String, unique=True, nullable=False, index=True)
    dataset_id = Column(String, ForeignKey("datasets.dataset_id", ondelete="CASCADE"), nullable=False, index=True)
    input = Column(JsonColumn, nullable=True)
    output = Column(JsonColumn, nullable=True)
    expected_output = Column(JsonColumn, nullable=True)
    context = Column(JsonColumn, nullable=True)
    source_trace_id = Column(String, nullable=True, index=True)
    source_observation_id = Column(String, nullable=True, index=True)
    status = Column(String(20), nullable=False, default="active", index=True)
    tags = Column(JsonColumn, nullable=True)
    metadata_json = Column("metadata", JsonColumn, nullable=True)
    is_archived = Column(Boolean, nullable=False, default=False, index=True)
    is_deleted = Column(Boolean, nullable=False, default=False, index=True)
    valid_from = Column(DateTime, nullable=True, index=True)
    valid_to = Column(DateTime, nullable=True, index=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))

    dataset = relationship("Dataset", back_populates="items")


class ExperimentRun(Base):
    __tablename__ = "experiment_runs"

    id = Column(Integer, primary_key=True, index=True)
    experiment_run_id = Column(String, unique=True, nullable=False, index=True)
    dataset_id = Column(String, ForeignKey("datasets.dataset_id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String, nullable=False, index=True)
    status = Column(String(20), nullable=False, default="pending", index=True)
    target_agent = Column(String, nullable=True, index=True)
    model = Column(String, nullable=True, index=True)
    prompt_name = Column(String, nullable=True, index=True)
    prompt_version = Column(String, nullable=True)
    runtime_config = Column(JsonColumn, nullable=True)
    metadata_json = Column("metadata", JsonColumn, nullable=True)
    total_count = Column(Integer, nullable=False, default=0)
    succeeded_count = Column(Integer, nullable=False, default=0)
    failed_count = Column(Integer, nullable=False, default=0)
    last_error = Column(Text, nullable=True)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))

    items = relationship("ExperimentRunItem", back_populates="run", cascade="all, delete-orphan")


class ExperimentRunItem(Base):
    __tablename__ = "experiment_run_items"
    __table_args__ = (UniqueConstraint("experiment_run_id", "dataset_item_id", name="uq_experiment_run_items_run_dataset_item"),)

    id = Column(Integer, primary_key=True, index=True)
    experiment_item_id = Column(String, unique=True, nullable=False, index=True)
    experiment_run_id = Column(String, ForeignKey("experiment_runs.experiment_run_id", ondelete="CASCADE"), nullable=False, index=True)
    dataset_item_id = Column(String, nullable=False, index=True)
    status = Column(String(20), nullable=False, default="pending", index=True)
    generated_output = Column(JsonColumn, nullable=True)
    generated_context = Column(JsonColumn, nullable=True)
    trace_id = Column(String, nullable=True, index=True)
    eval_run_id = Column(String, nullable=True, index=True)
    error = Column(Text, nullable=True)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))

    run = relationship("ExperimentRun", back_populates="items")


class PromptVersion(Base):
    __tablename__ = "prompt_versions"
    id        = Column(Integer, primary_key=True)
    name      = Column(String, nullable=False, index=True)
    hash      = Column(String(64), nullable=False)
    content   = Column(Text, nullable=False)
    synced_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    __table_args__ = (UniqueConstraint("name", "hash", name="uq_prompt_versions_name_hash"),)
