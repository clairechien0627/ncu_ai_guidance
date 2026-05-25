"""Add public_id to users table.

Revision ID: 040
Revises: 039
Create Date: 2026-05-24
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from alembic import op

revision = "040"
down_revision = "039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    op.add_column("users", sa.Column("public_id", sa.String(), nullable=True))
    users = bind.execute(sa.text("SELECT id FROM users WHERE public_id IS NULL")).fetchall()
    for (user_id,) in users:
        bind.execute(
            sa.text("UPDATE users SET public_id = :public_id WHERE id = :user_id"),
            {"public_id": str(uuid.uuid4()), "user_id": user_id},
        )
    op.alter_column("users", "public_id", nullable=False)
    op.create_unique_constraint("uq_users_public_id", "users", ["public_id"])
    op.create_index("ix_users_public_id", "users", ["public_id"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_users_public_id", table_name="users")
    op.drop_constraint("uq_users_public_id", "users", type_="unique")
    op.drop_column("users", "public_id")
