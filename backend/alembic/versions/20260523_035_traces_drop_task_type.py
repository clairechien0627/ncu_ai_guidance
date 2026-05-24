"""Drop task_type column from traces table

Revision ID: 035
Revises: 034
Create Date: 2026-05-23
"""
from alembic import op
import sqlalchemy as sa

revision = "035"
down_revision = "034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("ALTER TABLE traces DROP COLUMN IF EXISTS task_type;"))


def downgrade() -> None:
    op.execute(sa.text(
        "ALTER TABLE traces ADD COLUMN IF NOT EXISTS task_type VARCHAR(50);"
    ))
