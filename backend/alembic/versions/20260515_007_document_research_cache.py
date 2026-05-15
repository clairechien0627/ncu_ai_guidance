"""Add document_research_cache table for per-document coverage caching.

Revision ID: 007
Revises: 006
Create Date: 2026-05-15
"""
import sqlalchemy as sa
from alembic import op

revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    tables = {
        row[0]
        for row in conn.execute(
            sa.text("SELECT tablename FROM pg_tables WHERE schemaname='public'")
        )
    }
    if "document_research_cache" in tables:
        return

    op.create_table(
        "document_research_cache",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("document_id", sa.Integer(),
                  sa.ForeignKey("documents.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("coverage_hash", sa.String(32), nullable=False),
        sa.Column("slot_status", sa.Text(), nullable=True),
        sa.Column("evidence", sa.Text(), nullable=True),
        sa.Column("evidence_details", sa.Text(), nullable=True),
        sa.Column("known_keywords", sa.Text(), nullable=True),
        sa.Column("avoid_query_terms", sa.Text(), nullable=True),
        sa.Column("sources", sa.Text(), nullable=True),
        sa.Column("search_count", sa.Integer(), nullable=True, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("document_id", "coverage_hash", name="uq_doc_coverage"),
    )
    op.execute(sa.text(
        "CREATE INDEX idx_doc_research_cache_doc_id "
        "ON document_research_cache(document_id)"
    ))
    # Convert JSONB columns
    for col in ("slot_status", "evidence", "evidence_details",
                "known_keywords", "avoid_query_terms", "sources"):
        op.execute(sa.text(
            f"ALTER TABLE document_research_cache "
            f"ALTER COLUMN {col} TYPE JSONB "
            f"USING CASE WHEN {col} IS NULL OR trim({col}::text) = '' "
            f"THEN NULL ELSE {col}::jsonb END"
        ))


def downgrade():
    op.drop_table("document_research_cache")
