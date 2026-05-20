"""conversations: add user_id owner column

Revision ID: 028
Revises: 027
Create Date: 2026-05-20
"""

from alembic import op
import sqlalchemy as sa

revision = "028"
down_revision = "027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("conversations", sa.Column("user_id", sa.String(), nullable=True))
    op.create_index("ix_conversations_user_id", "conversations", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_conversations_user_id", "conversations")
    op.drop_column("conversations", "user_id")
