"""Add composite indexes for traces_v2 and scores.

Revision ID: 018
Revises: 017
Create Date: 2026-05-18
"""
import sqlalchemy as sa
from alembic import op

revision = "018"
down_revision = "017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # traces_v2: common filter combinations
    op.create_index(
        "ix_traces_v2_environment_start_time",
        "traces_v2", ["environment", sa.text("start_time DESC")],
    )
    op.create_index(
        "ix_traces_v2_user_id_start_time",
        "traces_v2", ["user_id", sa.text("start_time DESC")],
    )
    op.create_index(
        "ix_traces_v2_thread_id_start_time",
        "traces_v2", ["thread_id", sa.text("start_time DESC")],
    )

    # scores: common lookup pattern (fetch a named score for a trace)
    op.create_index(
        "ix_scores_trace_id_name",
        "scores", ["trace_id", "name"],
    )


def downgrade() -> None:
    op.drop_index("ix_scores_trace_id_name", table_name="scores")
    op.drop_index("ix_traces_v2_thread_id_start_time", table_name="traces_v2")
    op.drop_index("ix_traces_v2_user_id_start_time", table_name="traces_v2")
    op.drop_index("ix_traces_v2_environment_start_time", table_name="traces_v2")
