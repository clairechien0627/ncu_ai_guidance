"""Add prompt_versions table to track sync history.

Revision ID: 011
Revises: 010
Create Date: 2026-05-17
"""
import sqlalchemy as sa
from alembic import op

revision = "011"
down_revision = "010"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS prompt_versions (
            id         SERIAL PRIMARY KEY,
            name       VARCHAR NOT NULL,
            hash       VARCHAR(64) NOT NULL,
            content    TEXT NOT NULL,
            synced_at  TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        )
    """))
    conn.execute(sa.text(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_prompt_versions_name_hash "
        "ON prompt_versions (name, hash)"
    ))
    conn.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_prompt_versions_name "
        "ON prompt_versions (name, synced_at DESC)"
    ))


def downgrade():
    conn = op.get_bind()
    conn.execute(sa.text("DROP TABLE IF EXISTS prompt_versions"))
