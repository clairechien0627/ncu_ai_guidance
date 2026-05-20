"""conversations: add thread_id UUID, backfill from id

Revision ID: 027
Revises: 026
Create Date: 2026-05-20
"""

import uuid as _uuid
from alembic import op
import sqlalchemy as sa
from sqlalchemy.sql import text

revision = "027"
down_revision = "026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("conversations", sa.Column("thread_id", sa.String(36), nullable=True))
    op.create_index("ix_conversations_thread_id", "conversations", ["thread_id"], unique=True)

    # Backfill existing rows with a deterministic UUID derived from the integer id
    conn = op.get_bind()
    rows = conn.execute(text("SELECT id FROM conversations")).fetchall()
    for (row_id,) in rows:
        uid = str(_uuid.uuid5(_uuid.NAMESPACE_URL, f"conversation:{row_id}"))
        conn.execute(
            text("UPDATE conversations SET thread_id = :tid WHERE id = :id"),
            {"tid": uid, "id": row_id},
        )


def downgrade() -> None:
    op.drop_index("ix_conversations_thread_id", "conversations")
    op.drop_column("conversations", "thread_id")
