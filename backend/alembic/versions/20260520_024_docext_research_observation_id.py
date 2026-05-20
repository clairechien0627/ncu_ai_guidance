"""document_extractions: rename raw_research_run_id → research_observation_id, drop langsmith_run_id

Revision ID: 20260520_024
Revises: 20260520_023
Create Date: 2026-05-20
"""

from alembic import op
import sqlalchemy as sa

revision = "024"
down_revision = "023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "document_extractions",
        "raw_research_run_id",
        new_column_name="research_observation_id",
        existing_type=sa.String(),
        nullable=True,
    )
    op.drop_column("document_extractions", "langsmith_run_id")


def downgrade() -> None:
    op.add_column(
        "document_extractions",
        sa.Column("langsmith_run_id", sa.String(), nullable=True),
    )
    op.alter_column(
        "document_extractions",
        "research_observation_id",
        new_column_name="raw_research_run_id",
        existing_type=sa.String(),
        nullable=True,
    )
