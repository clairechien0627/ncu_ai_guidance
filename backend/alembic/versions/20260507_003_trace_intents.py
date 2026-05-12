"""Add original_intent, resolved_intent, quality_detail to traces

Revision ID: 003
Revises: 002
Create Date: 2026-05-07
"""
from alembic import op
import sqlalchemy as sa

revision = '003'
down_revision = '002'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    existing = {
        row[0] for row in conn.execute(sa.text(
            "SELECT column_name FROM information_schema.columns WHERE table_name='traces'"
        ))
    }
    if 'original_intent' not in existing:
        op.add_column('traces', sa.Column('original_intent', sa.String(), nullable=True))
    if 'resolved_intent' not in existing:
        op.add_column('traces', sa.Column('resolved_intent', sa.String(), nullable=True))
    if 'quality_detail' not in existing:
        op.add_column('traces', sa.Column('quality_detail', sa.Text(), nullable=True))

    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS idx_trace_original_intent "
        "ON traces(original_intent) WHERE original_intent IS NOT NULL"
    ))
    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS idx_trace_resolved_intent "
        "ON traces(resolved_intent) WHERE resolved_intent IS NOT NULL"
    ))


def downgrade():
    op.drop_index('idx_trace_resolved_intent', table_name='traces')
    op.drop_index('idx_trace_original_intent', table_name='traces')
    op.drop_column('traces', 'quality_detail')
    op.drop_column('traces', 'resolved_intent')
    op.drop_column('traces', 'original_intent')
