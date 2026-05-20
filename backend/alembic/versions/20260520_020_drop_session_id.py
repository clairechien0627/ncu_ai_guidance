"""Drop session_id from traces_v2 — thread_id is the single source of truth.

Revision ID: 020
Revises: 019
Create Date: 2026-05-20
"""
from alembic import op
import sqlalchemy as sa

revision = "020"
down_revision = "019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("traces_v2") as batch_op:
        batch_op.drop_index("ix_traces_v2_session_id")
        batch_op.drop_column("session_id")


def downgrade() -> None:
    with op.batch_alter_table("traces_v2") as batch_op:
        batch_op.add_column(sa.Column("session_id", sa.String(), nullable=True))
        batch_op.create_index("ix_traces_v2_session_id", ["session_id"])
