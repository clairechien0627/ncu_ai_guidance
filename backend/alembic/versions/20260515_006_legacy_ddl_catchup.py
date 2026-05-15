"""Legacy DDL catch-up: migrate all ADD COLUMN / CREATE INDEX statements
from create_tables() into Alembic.

Revision ID: 006
Revises: 005
Create Date: 2026-05-15

Every operation is idempotent (column/index existence checks + IF NOT EXISTS)
so this migration is safe to apply to databases in any state:
- Old production DB: create_tables() already ran these statements → no-ops.
- Fresh Docker DB:   create_all() created tables with current types → no-ops.
- Partially-migrated DB: only missing pieces are applied.

After this migration runs, create_tables() is reduced to
Base.metadata.create_all() only — no more ALTER TABLE at startup.
"""
import sqlalchemy as sa
from alembic import op

revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None

# JSONB columns that must be TEXT → JSONB converted if they were added as TEXT.
# (summary_json / tags / raw_research_sources on documents are handled by 001;
#  included here as a safety net for columns 001 may have skipped.)
_JSONB_COLS = [
    ("documents",  "summary_json"),
    ("documents",  "tags"),
    ("documents",  "raw_research_sources"),
    ("traces",     "prompt_stack_json"),
    ("traces",     "primary_prompt_json"),
    ("traces",     "workflow_prompts_json"),
]

# Indexes created by this migration (not already in 001 or 003).
_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_trace_prompt_name "
    "ON traces(prompt_name) WHERE prompt_name IS NOT NULL",

    "CREATE INDEX IF NOT EXISTS idx_trace_quality_score "
    "ON traces(quality_score) WHERE quality_score IS NOT NULL",

    "CREATE INDEX IF NOT EXISTS idx_trace_start_time_root "
    "ON traces(start_time DESC) WHERE parent_run_id IS NULL",

    "CREATE INDEX IF NOT EXISTS idx_documents_status ON documents(status)",
    "CREATE INDEX IF NOT EXISTS idx_documents_batch_status ON documents(batch_status)",
    "CREATE INDEX IF NOT EXISTS idx_documents_department_hint ON documents(department_hint)",
    "CREATE INDEX IF NOT EXISTS idx_documents_raw_research_run_id ON documents(raw_research_run_id)",
    "CREATE INDEX IF NOT EXISTS idx_documents_file_hash ON documents(file_hash)",

    "CREATE INDEX IF NOT EXISTS idx_documents_deleted_at "
    "ON documents(deleted_at) WHERE deleted_at IS NOT NULL",

    "CREATE INDEX IF NOT EXISTS idx_job_history_job_id ON job_history(job_id)",
    "CREATE INDEX IF NOT EXISTS idx_job_history_status ON job_history(status)",
    "CREATE INDEX IF NOT EXISTS idx_job_history_doc_status ON job_history(doc_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_traces_thread_id ON traces(thread_id)",
    "CREATE INDEX IF NOT EXISTS idx_traces_task_type ON traces(task_type)",
    "CREATE INDEX IF NOT EXISTS idx_traces_route_intent ON traces(route_intent)",
    "CREATE INDEX IF NOT EXISTS idx_traces_prompt_version ON traces(prompt_version)",
]

_INDEX_NAMES = [ddl.split("idx_")[1].split(" ")[0] for ddl in _INDEXES]  # for downgrade


def _cols(conn, table: str) -> set[str]:
    return {
        row[0]
        for row in conn.execute(
            sa.text("SELECT column_name FROM information_schema.columns WHERE table_name = :t"),
            {"t": table},
        )
    }


def upgrade():
    conn = op.get_bind()

    # ── conversations ──────────────────────────────────────────────────────────
    c = _cols(conn, "conversations")
    if "model" not in c:
        op.add_column("conversations", sa.Column("model", sa.String(), nullable=True, server_default="openai"))
    if "title" not in c:
        op.add_column("conversations", sa.Column("title", sa.String(), nullable=True))
    if "message_count" not in c:
        op.add_column("conversations", sa.Column("message_count", sa.Integer(), nullable=True, server_default="0"))

    # ── documents ─────────────────────────────────────────────────────────────
    c = _cols(conn, "documents")
    _doc_cols: list[tuple[str, sa.types.TypeEngine]] = [
        ("batch_status",        sa.String()),
        ("summary_json",        sa.Text()),
        ("category",            sa.String()),
        ("tags",                sa.Text()),
        ("department_hint",     sa.String()),
        ("error_message",       sa.Text()),
        ("langsmith_run_id",    sa.String()),
        ("raw_research_answer", sa.Text()),
        ("raw_research_sources",sa.Text()),
        ("raw_research_run_id", sa.String()),
        ("abstract_text",       sa.Text()),
        ("abstract_edited",     sa.Boolean()),
        ("quality_issue",       sa.String()),
        ("parser_used",         sa.String()),
        ("file_hash",           sa.String()),
        ("needs_reindex",       sa.Boolean()),
        ("deleted_at",          sa.DateTime()),
    ]
    _doc_defaults: dict[str, str] = {
        "batch_status":   "pending",
        "abstract_edited":"false",
        "needs_reindex":  "false",
    }
    for col_name, col_type in _doc_cols:
        if col_name not in c:
            kw: dict = {"nullable": True}
            if col_name in _doc_defaults:
                kw["server_default"] = _doc_defaults[col_name]
            op.add_column("documents", sa.Column(col_name, col_type, **kw))

    # ── job_history ────────────────────────────────────────────────────────────
    c = _cols(conn, "job_history")
    _job_cols: list[tuple[str, sa.types.TypeEngine, dict]] = [
        ("job_id",     sa.String(),   {}),
        ("status",     sa.String(),   {"server_default": "done"}),
        ("stage",      sa.Text(),     {}),
        ("stage_log",  sa.Text(),     {}),
        ("error",      sa.Text(),     {}),
        ("started_at", sa.DateTime(), {}),
        ("updated_at", sa.DateTime(), {}),
    ]
    for col_name, col_type, kw in _job_cols:
        if col_name not in c:
            op.add_column("job_history", sa.Column(col_name, col_type, nullable=True, **kw))

    # ── traces ────────────────────────────────────────────────────────────────
    c = _cols(conn, "traces")

    # Drop legacy column added before the agent routing refactor.
    if "mode" in c:
        op.drop_column("traces", "mode")

    _trace_cols: list[tuple[str, sa.types.TypeEngine]] = [
        ("task_type",             sa.String()),
        ("route_intent",          sa.String()),
        ("agent_name",            sa.String()),
        ("prompt_name",           sa.String()),
        ("prompt_version",        sa.String()),
        ("base_prompt_name",      sa.String()),
        ("task_prompt_name",      sa.String()),
        ("quality_prompt_name",   sa.String()),
        ("base_prompt_hash",      sa.String()),
        ("task_prompt_hash",      sa.String()),
        ("quality_prompt_hash",   sa.String()),
        ("prompt_stack_name",     sa.String()),
        ("prompt_stack_json",     sa.Text()),
        ("primary_prompt_json",   sa.Text()),
        ("workflow_prompts_json", sa.Text()),
        ("prompt_stack_tokens",   sa.Integer()),
        ("tool_count",            sa.Integer()),
        ("llm_call_count",        sa.Integer()),
        ("quality_score",         sa.Float()),
        ("user_feedback",         sa.Text()),
    ]
    for col_name, col_type in _trace_cols:
        if col_name not in c:
            op.add_column("traces", sa.Column(col_name, col_type, nullable=True))

    # ── TEXT → JSONB conversions ───────────────────────────────────────────────
    for table, col in _JSONB_COLS:
        row = conn.execute(sa.text(
            "SELECT data_type FROM information_schema.columns "
            "WHERE table_name = :t AND column_name = :c"
        ), {"t": table, "c": col}).fetchone()
        if row and row[0] == "text":
            op.execute(sa.text(
                f"ALTER TABLE {table} ALTER COLUMN {col} TYPE JSONB "
                f"USING CASE WHEN {col} IS NULL OR trim({col}::text) = '' "
                f"THEN NULL ELSE {col}::jsonb END"
            ))

    # ── Indexes ────────────────────────────────────────────────────────────────
    for ddl in _INDEXES:
        op.execute(sa.text(ddl))


def downgrade():
    # Drop indexes created by this migration.
    for name in _INDEX_NAMES:
        op.execute(sa.text(f"DROP INDEX IF EXISTS idx_{name}"))

    # Revert TEXT JSON columns that this migration may have converted to JSONB.
    # (Only the three not already handled by migration 001's downgrade.)
    for table, col in [
        ("traces", "workflow_prompts_json"),
        ("traces", "primary_prompt_json"),
        ("traces", "prompt_stack_json"),
    ]:
        row = op.get_bind().execute(sa.text(
            "SELECT data_type FROM information_schema.columns "
            "WHERE table_name = :t AND column_name = :c"
        ), {"t": table, "c": col}).fetchone()
        if row and row[0] == "jsonb":
            op.execute(sa.text(
                f"ALTER TABLE {table} ALTER COLUMN {col} TYPE TEXT USING {col}::text"
            ))

    # Drop columns added by this migration.
    # WARNING: this is destructive — only run downgrade if you can restore data from backup.
    for col in ("model", "title", "message_count"):
        op.drop_column("conversations", col)
    for col in (
        "batch_status", "summary_json", "category", "tags", "department_hint",
        "error_message", "langsmith_run_id", "raw_research_answer", "raw_research_sources",
        "raw_research_run_id", "abstract_text", "abstract_edited", "quality_issue",
        "parser_used", "file_hash", "needs_reindex", "deleted_at",
    ):
        op.drop_column("documents", col)
    for col in ("job_id", "status", "stage", "stage_log", "error", "started_at", "updated_at"):
        op.drop_column("job_history", col)
    for col in (
        "task_type", "route_intent", "agent_name", "prompt_name", "prompt_version",
        "base_prompt_name", "task_prompt_name", "quality_prompt_name",
        "base_prompt_hash", "task_prompt_hash", "quality_prompt_hash",
        "prompt_stack_name", "prompt_stack_json", "primary_prompt_json",
        "workflow_prompts_json", "prompt_stack_tokens", "tool_count",
        "llm_call_count", "quality_score", "user_feedback",
    ):
        op.drop_column("traces", col)
