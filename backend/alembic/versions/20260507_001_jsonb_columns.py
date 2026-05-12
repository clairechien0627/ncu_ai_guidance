"""Convert JSON text columns to JSONB

Revision ID: 001
Revises:
Create Date: 2026-05-07

Converts the following TEXT columns that store JSON to JSONB so that:
- GIN indexes can be built on their contents
- PostgreSQL can validate JSON at write time
- ->> / @> operators become available for future queries

The TypeDecorator in database.py (JsonColumn) keeps the Python interface
as JSON strings, so no application code changes are needed.
"""
from alembic import op

revision = "001"
down_revision = None
branch_labels = None
depends_on = None

# (table, column) pairs to migrate TEXT → JSONB
_COLUMNS = [
    ("traces", "document_ids"),
    ("traces", "prompt_stack_json"),
    ("traces", "display"),
    ("traces", "inputs"),
    ("traces", "outputs"),
    ("documents", "summary_json"),
    ("documents", "tags"),
    ("documents", "raw_research_sources"),
]


def _col_is_text(conn, table: str, col: str) -> bool:
    result = conn.execute(
        __import__("sqlalchemy").text(
            "SELECT data_type FROM information_schema.columns "
            "WHERE table_name = :t AND column_name = :c"
        ),
        {"t": table, "c": col},
    ).fetchone()
    return bool(result and result[0] == "text")


def upgrade():
    conn = op.get_bind()
    for table, col in _COLUMNS:
        if _col_is_text(conn, table, col):
            op.execute(
                f"""
                ALTER TABLE {table}
                ALTER COLUMN {col} TYPE JSONB
                USING CASE
                    WHEN {col} IS NULL OR trim({col}) = '' THEN NULL
                    ELSE {col}::jsonb
                END
                """
            )

    # GIN index on traces.display for fast JSON-path lookups on the answer field.
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_traces_display_gin "
        "ON traces USING GIN (display) WHERE display IS NOT NULL"
    )
    # GIN index on traces.document_ids for filtering by doc id.
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_traces_document_ids_gin "
        "ON traces USING GIN (document_ids) WHERE document_ids IS NOT NULL"
    )


def downgrade():
    op.execute("DROP INDEX IF EXISTS idx_traces_display_gin")
    op.execute("DROP INDEX IF EXISTS idx_traces_document_ids_gin")

    conn = op.get_bind()
    for table, col in _COLUMNS:
        result = conn.execute(
            __import__("sqlalchemy").text(
                "SELECT data_type FROM information_schema.columns "
                "WHERE table_name = :t AND column_name = :c"
            ),
            {"t": table, "c": col},
        ).fetchone()
        if result and result[0] == "jsonb":
            op.execute(
                f"ALTER TABLE {table} ALTER COLUMN {col} TYPE TEXT USING {col}::text"
            )
