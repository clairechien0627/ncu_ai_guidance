"""Add context_summary to conversations

Revision ID: 004
Revises: 003
Create Date: 2026-05-13
"""
from alembic import op
import sqlalchemy as sa

revision = '004'
down_revision = '003'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    existing = {
        row[0] for row in conn.execute(sa.text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='conversations'"
        ))
    }
    if 'context_summary' not in existing:
        op.add_column('conversations', sa.Column('context_summary', sa.Text(), nullable=True))


def downgrade():
    op.drop_column('conversations', 'context_summary')
