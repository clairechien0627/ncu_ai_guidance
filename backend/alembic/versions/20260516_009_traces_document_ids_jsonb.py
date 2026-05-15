"""Cast traces.document_ids from text to jsonb and add GIN index.

Revision ID: 009
Revises: 008
Create Date: 2026-05-16
"""
import sqlalchemy as sa
from alembic import op

revision = "009"
down_revision = "008"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()

    # Check current column type
    row = conn.execute(sa.text(
        "SELECT data_type FROM information_schema.columns "
        "WHERE table_name = 'traces' AND column_name = 'document_ids'"
    )).fetchone()
    if row and row[0] == "jsonb":
        return  # already jsonb, nothing to do

    # Cast text -> jsonb (NULL-safe: NULL stays NULL, empty string becomes NULL)
    conn.execute(sa.text(
        "ALTER TABLE traces "
        "ALTER COLUMN document_ids TYPE jsonb "
        "USING CASE WHEN document_ids IS NULL OR document_ids = '' THEN NULL "
        "           ELSE document_ids::jsonb END"
    ))

    # GIN index for @> containment queries
    conn.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS idx_traces_document_ids_gin "
        "ON traces USING gin(document_ids)"
    ))


def downgrade():
    conn = op.get_bind()
    conn.execute(sa.text(
        "DROP INDEX IF EXISTS idx_traces_document_ids_gin"
    ))
    conn.execute(sa.text(
        "ALTER TABLE traces "
        "ALTER COLUMN document_ids TYPE text "
        "USING document_ids::text"
    ))
