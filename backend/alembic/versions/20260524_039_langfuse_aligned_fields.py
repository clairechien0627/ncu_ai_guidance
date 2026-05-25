"""Add Langfuse-aligned trace fields.

Revision ID: 039
Revises: 038
Create Date: 2026-05-24
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "039"
down_revision = "038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    json_type = postgresql.JSONB(astext_type=sa.Text())

    op.add_column("traces_v2", sa.Column("bookmarked", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_index("ix_traces_v2_bookmarked", "traces_v2", ["bookmarked"])

    op.add_column("observations", sa.Column("environment", sa.String(length=40), nullable=False, server_default="default"))
    op.add_column("observations", sa.Column("prompt_id", sa.String(), nullable=True))
    op.add_column("observations", sa.Column("tool_calls", json_type, nullable=True))
    op.add_column("observations", sa.Column("tool_definitions", json_type, nullable=True))
    op.add_column("observations", sa.Column("tool_call_names", json_type, nullable=True))
    op.create_index("ix_observations_environment", "observations", ["environment"])
    op.create_index("ix_observations_prompt_id", "observations", ["prompt_id"])

    op.add_column("scores", sa.Column("environment", sa.String(length=40), nullable=False, server_default="default"))
    op.add_column("scores", sa.Column("thread_id", sa.String(), nullable=True))
    op.create_index("ix_scores_environment", "scores", ["environment"])
    op.create_index("ix_scores_thread_id", "scores", ["thread_id"])

    op.add_column("datasets", sa.Column("input_schema", json_type, nullable=True))
    op.add_column("datasets", sa.Column("expected_output_schema", json_type, nullable=True))


def downgrade() -> None:
    op.drop_column("datasets", "expected_output_schema")
    op.drop_column("datasets", "input_schema")

    op.drop_index("ix_scores_thread_id", table_name="scores")
    op.drop_index("ix_scores_environment", table_name="scores")
    op.drop_column("scores", "thread_id")
    op.drop_column("scores", "environment")

    op.drop_index("ix_observations_prompt_id", table_name="observations")
    op.drop_index("ix_observations_environment", table_name="observations")
    op.drop_column("observations", "tool_call_names")
    op.drop_column("observations", "tool_definitions")
    op.drop_column("observations", "tool_calls")
    op.drop_column("observations", "prompt_id")
    op.drop_column("observations", "environment")

    op.drop_index("ix_traces_v2_bookmarked", table_name="traces_v2")
    op.drop_column("traces_v2", "bookmarked")
