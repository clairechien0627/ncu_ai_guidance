"""Add unique constraint on (document_id, version) in document_extractions

Revision ID: 005
Revises: 004
Create Date: 2026-05-14
"""
from alembic import op
import sqlalchemy as sa

revision = '005'
down_revision = '004'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()

    # Check if the constraint already exists (idempotent).
    existing = {
        row[0]
        for row in conn.execute(sa.text(
            "SELECT constraint_name FROM information_schema.table_constraints "
            "WHERE table_name='document_extractions' AND constraint_type='UNIQUE'"
        ))
    }
    if 'uq_document_extractions_document_id_version' not in existing:
        op.create_unique_constraint(
            'uq_document_extractions_document_id_version',
            'document_extractions',
            ['document_id', 'version'],
        )


def downgrade():
    op.drop_constraint(
        'uq_document_extractions_document_id_version',
        'document_extractions',
        type_='unique',
    )
