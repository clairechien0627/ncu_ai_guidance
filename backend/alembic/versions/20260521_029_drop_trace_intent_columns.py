"""traces: drop route_intent / original_intent / resolved_intent columns

Routing is now driven by agent_name directly. route_intent now stores
the routed agent_name value (e.g. "retrieval_agent") for backward compat
during transition, but original_intent and resolved_intent are removed.

Revision ID: 029
Revises: 028
Create Date: 2026-05-21
"""

from alembic import op
import sqlalchemy as sa

revision = "029"
down_revision = "028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index("ix_traces_original_intent", table_name="traces", if_exists=True)
    op.drop_index("ix_traces_resolved_intent", table_name="traces", if_exists=True)
    op.drop_column("traces", "original_intent")
    op.drop_column("traces", "resolved_intent")


def downgrade() -> None:
    op.add_column("traces", sa.Column("original_intent", sa.String(), nullable=True))
    op.add_column("traces", sa.Column("resolved_intent", sa.String(), nullable=True))
    op.create_index("ix_traces_original_intent", "traces", ["original_intent"])
    op.create_index("ix_traces_resolved_intent", "traces", ["resolved_intent"])
