"""conversations: add last_agent_name column

Stores the agent_name used in the most recent turn, so route_request()
can pass previous_agent_name without querying the trace system.

Revision ID: 031
Revises: 030
Create Date: 2026-05-21
"""

from alembic import op
import sqlalchemy as sa

revision = "031"
down_revision = "030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("conversations", sa.Column("last_agent_name", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("conversations", "last_agent_name")
