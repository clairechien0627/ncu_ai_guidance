"""Rename traces.parent_run_id → traces.trace_id for naming consistency.

Revision ID: 022
Revises: 021
Create Date: 2026-05-20
"""
from alembic import op

revision = "022"
down_revision = "021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("traces") as batch_op:
        batch_op.alter_column("parent_run_id", new_column_name="trace_id")


def downgrade() -> None:
    with op.batch_alter_table("traces") as batch_op:
        batch_op.alter_column("trace_id", new_column_name="parent_run_id")
