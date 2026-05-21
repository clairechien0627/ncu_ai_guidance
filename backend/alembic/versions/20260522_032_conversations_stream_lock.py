"""conversations: add stream_started_at for mid-run steering detection

Stores the timestamp when a streaming response started so that concurrent
requests from the same thread can be redirected to the steering queue instead
of launching a second parallel run.

Revision ID: 032
Revises: 031
Create Date: 2026-05-22
"""

from alembic import op
import sqlalchemy as sa

revision = "032"
down_revision = "031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column("stream_started_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("conversations", "stream_started_at")
