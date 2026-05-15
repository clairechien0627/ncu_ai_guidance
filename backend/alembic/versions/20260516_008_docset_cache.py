"""Extend document_research_cache to support multi-document sets.

Adds document_set_hash column as the new primary lookup key (MD5 of sorted
comma-joined document IDs), migrates existing single-doc rows, swaps the
unique constraint, and makes document_id nullable.

Revision ID: 008
Revises: 007
Create Date: 2026-05-16
"""
import sqlalchemy as sa
from alembic import op

revision = "008"
down_revision = "007"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()

    # Check if column already exists (idempotent)
    existing = {
        row[0]
        for row in conn.execute(sa.text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'document_research_cache'"
        ))
    }

    # 1. Add document_set_hash as nullable first
    if "document_set_hash" not in existing:
        op.add_column("document_research_cache",
                      sa.Column("document_set_hash", sa.String(32), nullable=True))

    # 2. Populate from existing document_id rows using PostgreSQL md5()
    conn.execute(sa.text(
        "UPDATE document_research_cache "
        "SET document_set_hash = md5(document_id::text) "
        "WHERE document_set_hash IS NULL AND document_id IS NOT NULL"
    ))

    # 3. Make NOT NULL
    conn.execute(sa.text(
        "ALTER TABLE document_research_cache "
        "ALTER COLUMN document_set_hash SET NOT NULL"
    ))

    # 4. Add index on document_set_hash
    conn.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS idx_docset_hash "
        "ON document_research_cache(document_set_hash)"
    ))

    # 5. Drop old unique constraint and add new one
    constraints = {
        row[0]
        for row in conn.execute(sa.text(
            "SELECT constraint_name FROM information_schema.table_constraints "
            "WHERE table_name = 'document_research_cache' AND constraint_type = 'UNIQUE'"
        ))
    }
    if "uq_doc_coverage" in constraints:
        op.drop_constraint("uq_doc_coverage", "document_research_cache", type_="unique")
    if "uq_docset_coverage" not in constraints:
        op.create_unique_constraint(
            "uq_docset_coverage", "document_research_cache",
            ["document_set_hash", "coverage_hash"]
        )

    # 6. Make document_id nullable (multi-doc rows won't have a single FK)
    conn.execute(sa.text(
        "ALTER TABLE document_research_cache "
        "ALTER COLUMN document_id DROP NOT NULL"
    ))


def downgrade():
    conn = op.get_bind()
    # Restore original constraint (only safe if no multi-doc rows exist)
    try:
        op.drop_constraint("uq_docset_coverage", "document_research_cache", type_="unique")
    except Exception:
        pass
    try:
        op.create_unique_constraint(
            "uq_doc_coverage", "document_research_cache",
            ["document_id", "coverage_hash"]
        )
    except Exception:
        pass
    conn.execute(sa.text(
        "ALTER TABLE document_research_cache ALTER COLUMN document_id SET NOT NULL"
    ))
    op.drop_column("document_research_cache", "document_set_hash")
