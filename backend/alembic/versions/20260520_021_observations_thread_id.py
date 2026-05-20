"""Add thread_id to observations for direct querying without JOIN.

Revision ID: 021
Revises: 020
Create Date: 2026-05-20
"""
from alembic import op
import sqlalchemy as sa

revision = "021"
down_revision = "020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("observations") as batch_op:
        batch_op.add_column(sa.Column("thread_id", sa.String(), nullable=True))
        batch_op.create_index("ix_observations_thread_id", ["thread_id"])


def downgrade() -> None:
    with op.batch_alter_table("observations") as batch_op:
        batch_op.drop_index("ix_observations_thread_id")
        batch_op.drop_column("thread_id")
