"""Add environment and user_id columns to traces.

Revision ID: 010
Revises: 009
Create Date: 2026-05-17
"""
import sqlalchemy as sa
from alembic import op

revision = "010"
down_revision = "009"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()

    # Add environment column (default 'default', backfill existing rows)
    conn.execute(sa.text(
        "ALTER TABLE traces ADD COLUMN IF NOT EXISTS environment VARCHAR(40) "
        "NOT NULL DEFAULT 'default'"
    ))
    conn.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_trace_environment ON traces (environment)"
    ))

    # Add user_id column (nullable)
    conn.execute(sa.text(
        "ALTER TABLE traces ADD COLUMN IF NOT EXISTS user_id VARCHAR NULLABLE"
    ))
    conn.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_trace_user_id ON traces (user_id)"
    ))


def downgrade():
    conn = op.get_bind()
    conn.execute(sa.text("DROP INDEX IF EXISTS ix_trace_user_id"))
    conn.execute(sa.text("ALTER TABLE traces DROP COLUMN IF EXISTS user_id"))
    conn.execute(sa.text("DROP INDEX IF EXISTS ix_trace_environment"))
    conn.execute(sa.text("ALTER TABLE traces DROP COLUMN IF EXISTS environment"))
