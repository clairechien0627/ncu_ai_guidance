"""Create document_extractions table and migrate data

Revision ID: 002
Revises: 001
Create Date: 2026-05-07

Creates document_extractions table to hold extraction pipeline results
separately from core document metadata. Migrates existing data from the
deprecated columns on documents.
"""
import sqlalchemy as sa
from alembic import op

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "document_extractions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("document_id", sa.Integer(), sa.ForeignKey("documents.id"), nullable=False, index=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("summary_json", sa.Text(), nullable=True),
        sa.Column("tags", sa.Text(), nullable=True),
        sa.Column("category", sa.VARCHAR(), nullable=True),
        sa.Column("department_hint", sa.VARCHAR(), nullable=True),
        sa.Column("raw_research_answer", sa.Text(), nullable=True),
        sa.Column("raw_research_sources", sa.Text(), nullable=True),
        sa.Column("raw_research_run_id", sa.VARCHAR(), nullable=True),
        sa.Column("langsmith_run_id", sa.VARCHAR(), nullable=True),
    )
    op.create_index(
        "idx_doc_extractions_doc_version",
        "document_extractions",
        ["document_id", "version"],
    )

    # Copy existing extraction data from documents → document_extractions.
    # Only migrate documents that have at least one non-null extraction field.
    op.execute("""
        INSERT INTO document_extractions
            (document_id, version, summary_json, tags, category, department_hint,
             raw_research_answer, raw_research_sources, raw_research_run_id, langsmith_run_id)
        SELECT
            id, 1, summary_json, tags, category, department_hint,
            raw_research_answer, raw_research_sources, raw_research_run_id, langsmith_run_id
        FROM documents
        WHERE
            summary_json IS NOT NULL
            OR raw_research_answer IS NOT NULL
            OR category IS NOT NULL
    """)

    # Convert the JSON text columns in document_extractions to JSONB.
    for col in ("summary_json", "tags", "raw_research_sources"):
        op.execute(f"""
            DO $$ BEGIN
                IF EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name='document_extractions' AND column_name='{col}'
                    AND data_type='text'
                ) THEN
                    ALTER TABLE document_extractions ALTER COLUMN {col} TYPE JSONB
                    USING CASE WHEN {col} IS NULL OR trim({col}) = '' THEN NULL
                               ELSE {col}::jsonb END;
                END IF;
            END $$;
        """)


def downgrade():
    op.drop_index("idx_doc_extractions_doc_version", table_name="document_extractions")
    op.drop_table("document_extractions")
