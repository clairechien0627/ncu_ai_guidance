"""traces: drop route_intent column

route_intent column stored agent_name values during transition but
is now superseded by the agent_name column. All filtering and grouping
uses agent_name directly.

Revision ID: 030
Revises: 029
Create Date: 2026-05-21
"""

from alembic import op
import sqlalchemy as sa

revision = "030"
down_revision = "029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index("ix_traces_route_intent", table_name="traces", if_exists=True)
    op.drop_column("traces", "route_intent")


def downgrade() -> None:
    op.add_column("traces", sa.Column("route_intent", sa.String(), nullable=True))
    op.create_index("ix_traces_route_intent", "traces", ["route_intent"])
