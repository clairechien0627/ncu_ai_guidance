"""agent_messages: drop redundant task_type column (agent_name is the type)

Revision ID: 034
Revises: 033
Create Date: 2026-05-23
"""

from alembic import op
import sqlalchemy as sa

revision = "034"
down_revision = "033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text(
        "ALTER TABLE agent_messages DROP COLUMN IF EXISTS task_type;"
    ))


def downgrade() -> None:
    op.execute(sa.text(
        "ALTER TABLE agent_messages ADD COLUMN IF NOT EXISTS task_type VARCHAR(50);"
    ))
