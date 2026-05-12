import json as _json
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Text, Boolean, Float, ForeignKey
from sqlalchemy import TypeDecorator
from sqlalchemy.orm import sessionmaker, DeclarativeBase, relationship
from datetime import datetime, timezone

from config import settings

engine = create_engine(settings.database_url)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


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
    # Extraction result fields — deprecated; new writes go to DocumentExtraction.
    # Kept for backward compat until all read paths migrate.
    summary_json = Column(JsonColumn, nullable=True)
    category = Column(String, nullable=True)
    tags = Column(JsonColumn, nullable=True)
    department_hint = Column(String, nullable=True)
    langsmith_run_id = Column(String, nullable=True)
    raw_research_answer = Column(Text, nullable=True)
    raw_research_sources = Column(JsonColumn, nullable=True)
    raw_research_run_id = Column(String, nullable=True)
    # Document metadata
    abstract_text = Column(Text, nullable=True)
    abstract_edited = Column(Boolean, default=False)
    quality_issue = Column(String, nullable=True)
    parser_used = Column(String, nullable=True)
    file_hash = Column(String, nullable=True, index=True)
    needs_reindex = Column(Boolean, default=False)
    deleted_at = Column(DateTime, nullable=True)

    extraction = relationship(
        "DocumentExtraction",
        back_populates="document",
        uselist=False,
        primaryjoin="Document.id == foreign(DocumentExtraction.document_id)",
        order_by="DocumentExtraction.version.desc()",
    )


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


class Conversation(Base):
    __tablename__ = "conversations"
    id = Column(Integer, primary_key=True, index=True)
    model = Column(String, default="openai")
    title = Column(String, nullable=True)
    message_count = Column(Integer, default=0)
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
    mode = Column(String, nullable=True, index=True)
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
    prompt_stack_tokens = Column(Integer, nullable=True)
    tool_count = Column(Integer, nullable=True)
    llm_call_count = Column(Integer, nullable=True)
    quality_score = Column(Float, nullable=True)
    user_feedback = Column(Text, nullable=True)
    display = Column(JsonColumn, nullable=True)         # JSON TraceDisplay（僅 root run）
    original_intent = Column(String, nullable=True, index=True)  # LLM 原始意圖決策
    resolved_intent = Column(String, nullable=True, index=True)  # normalize 後最終路由
    quality_detail = Column(JsonColumn, nullable=True)           # {"grounding": 4.2, "issues": [...]}


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def create_tables():
    Base.metadata.create_all(bind=engine)
    from sqlalchemy import text
    with engine.connect() as conn:
        conn.execute(text("ALTER TABLE conversations ADD COLUMN IF NOT EXISTS model VARCHAR DEFAULT 'openai'"))
        conn.execute(text("ALTER TABLE conversations ADD COLUMN IF NOT EXISTS title VARCHAR"))
        conn.execute(text("ALTER TABLE conversations ADD COLUMN IF NOT EXISTS message_count INTEGER DEFAULT 0"))
        # Batch pipeline columns
        conn.execute(text("ALTER TABLE documents ADD COLUMN IF NOT EXISTS batch_status VARCHAR DEFAULT 'pending'"))
        conn.execute(text("ALTER TABLE documents ADD COLUMN IF NOT EXISTS summary_json TEXT"))
        conn.execute(text("ALTER TABLE documents ADD COLUMN IF NOT EXISTS category VARCHAR"))
        conn.execute(text("ALTER TABLE documents ADD COLUMN IF NOT EXISTS tags TEXT"))
        conn.execute(text("ALTER TABLE documents ADD COLUMN IF NOT EXISTS department_hint VARCHAR"))
        conn.execute(text("ALTER TABLE documents ADD COLUMN IF NOT EXISTS error_message TEXT"))
        conn.execute(text("ALTER TABLE documents ADD COLUMN IF NOT EXISTS langsmith_run_id VARCHAR"))
        conn.execute(text("ALTER TABLE documents ADD COLUMN IF NOT EXISTS raw_research_answer TEXT"))
        conn.execute(text("ALTER TABLE documents ADD COLUMN IF NOT EXISTS raw_research_sources TEXT"))
        conn.execute(text("ALTER TABLE documents ADD COLUMN IF NOT EXISTS raw_research_run_id VARCHAR"))
        conn.execute(text("ALTER TABLE documents ADD COLUMN IF NOT EXISTS abstract_text TEXT"))
        conn.execute(text("ALTER TABLE documents ADD COLUMN IF NOT EXISTS abstract_edited BOOLEAN DEFAULT FALSE"))
        conn.execute(text("ALTER TABLE documents ADD COLUMN IF NOT EXISTS quality_issue VARCHAR"))
        conn.execute(text("ALTER TABLE documents ADD COLUMN IF NOT EXISTS parser_used VARCHAR"))
        conn.execute(text("ALTER TABLE documents ADD COLUMN IF NOT EXISTS file_hash VARCHAR"))
        conn.execute(text("ALTER TABLE documents ADD COLUMN IF NOT EXISTS needs_reindex BOOLEAN DEFAULT FALSE"))
        conn.execute(text("ALTER TABLE documents ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMP"))
        conn.execute(text("ALTER TABLE job_history ADD COLUMN IF NOT EXISTS job_id VARCHAR"))
        conn.execute(text("ALTER TABLE job_history ADD COLUMN IF NOT EXISTS status VARCHAR DEFAULT 'done'"))
        conn.execute(text("ALTER TABLE job_history ADD COLUMN IF NOT EXISTS stage TEXT"))
        conn.execute(text("ALTER TABLE job_history ADD COLUMN IF NOT EXISTS stage_log TEXT"))
        conn.execute(text("ALTER TABLE job_history ADD COLUMN IF NOT EXISTS error TEXT"))
        conn.execute(text("ALTER TABLE job_history ADD COLUMN IF NOT EXISTS started_at TIMESTAMP"))
        conn.execute(text("ALTER TABLE job_history ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP"))
        conn.execute(text("ALTER TABLE traces ADD COLUMN IF NOT EXISTS mode VARCHAR"))
        conn.execute(text("ALTER TABLE traces ADD COLUMN IF NOT EXISTS agent_name VARCHAR"))
        conn.execute(text("ALTER TABLE traces ADD COLUMN IF NOT EXISTS prompt_name VARCHAR"))
        conn.execute(text("ALTER TABLE traces ADD COLUMN IF NOT EXISTS prompt_version VARCHAR"))
        conn.execute(text("ALTER TABLE traces ADD COLUMN IF NOT EXISTS base_prompt_name VARCHAR"))
        conn.execute(text("ALTER TABLE traces ADD COLUMN IF NOT EXISTS task_prompt_name VARCHAR"))
        conn.execute(text("ALTER TABLE traces ADD COLUMN IF NOT EXISTS quality_prompt_name VARCHAR"))
        conn.execute(text("ALTER TABLE traces ADD COLUMN IF NOT EXISTS base_prompt_hash VARCHAR"))
        conn.execute(text("ALTER TABLE traces ADD COLUMN IF NOT EXISTS task_prompt_hash VARCHAR"))
        conn.execute(text("ALTER TABLE traces ADD COLUMN IF NOT EXISTS quality_prompt_hash VARCHAR"))
        conn.execute(text("ALTER TABLE traces ADD COLUMN IF NOT EXISTS prompt_stack_name VARCHAR"))
        conn.execute(text("ALTER TABLE traces ADD COLUMN IF NOT EXISTS prompt_stack_json TEXT"))
        conn.execute(text("ALTER TABLE traces ADD COLUMN IF NOT EXISTS prompt_stack_tokens INTEGER"))
        conn.execute(text("ALTER TABLE traces ADD COLUMN IF NOT EXISTS tool_count INTEGER"))
        conn.execute(text("ALTER TABLE traces ADD COLUMN IF NOT EXISTS llm_call_count INTEGER"))
        conn.execute(text("ALTER TABLE traces ADD COLUMN IF NOT EXISTS quality_score FLOAT"))
        conn.execute(text("ALTER TABLE traces ADD COLUMN IF NOT EXISTS user_feedback TEXT"))
        conn.execute(text("ALTER TABLE traces ADD COLUMN IF NOT EXISTS original_intent VARCHAR"))
        conn.execute(text("ALTER TABLE traces ADD COLUMN IF NOT EXISTS resolved_intent VARCHAR"))
        conn.execute(text("ALTER TABLE traces ADD COLUMN IF NOT EXISTS quality_detail TEXT"))
        # Performance indexes for trace monitor queries
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_trace_prompt_name "
            "ON traces(prompt_name) WHERE prompt_name IS NOT NULL"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_trace_quality_score "
            "ON traces(quality_score) WHERE quality_score IS NOT NULL"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_trace_start_time_root "
            "ON traces(start_time DESC) WHERE parent_run_id IS NULL"
        ))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_documents_status ON documents(status)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_documents_batch_status ON documents(batch_status)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_documents_department_hint ON documents(department_hint)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_documents_raw_research_run_id ON documents(raw_research_run_id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_documents_file_hash ON documents(file_hash)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_documents_deleted_at ON documents(deleted_at) WHERE deleted_at IS NOT NULL"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_job_history_job_id ON job_history(job_id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_job_history_status ON job_history(status)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_job_history_doc_status ON job_history(doc_id, status)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_traces_thread_id ON traces(thread_id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_traces_mode ON traces(mode)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_traces_prompt_version ON traces(prompt_version)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_trace_original_intent ON traces(original_intent) WHERE original_intent IS NOT NULL"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_trace_resolved_intent ON traces(resolved_intent) WHERE resolved_intent IS NOT NULL"))
        # JSON column type conversions (TEXT → JSONB) and GIN indexes are handled
        # by Alembic migrations in alembic/versions/.
        conn.commit()
