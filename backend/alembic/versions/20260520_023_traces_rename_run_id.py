"""Rename traces.run_id → traces.observation_id for naming consistency.

Revision ID: 023
Revises: 022
Create Date: 2026-05-20
"""
from alembic import op

revision = "023"
down_revision = "022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("traces") as batch_op:
        batch_op.alter_column("run_id", new_column_name="observation_id")


def downgrade() -> None:
    with op.batch_alter_table("traces") as batch_op:
        batch_op.alter_column("observation_id", new_column_name="run_id")
