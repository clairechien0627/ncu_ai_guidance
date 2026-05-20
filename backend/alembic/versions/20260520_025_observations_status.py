"""observations: add status column

Revision ID: 025
Revises: 024
Create Date: 2026-05-20
"""

from alembic import op
import sqlalchemy as sa

revision = "025"
down_revision = "024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "observations",
        sa.Column("status", sa.String(20), nullable=True),
    )
    op.create_index("ix_observations_status", "observations", ["status"])
    # Backfill existing rows
    op.execute("UPDATE observations SET status = 'error' WHERE level = 'ERROR'")
    op.execute("UPDATE observations SET status = 'success' WHERE level != 'ERROR' OR level IS NULL")


def downgrade() -> None:
    op.drop_index("ix_observations_status", "observations")
    op.drop_column("observations", "status")
