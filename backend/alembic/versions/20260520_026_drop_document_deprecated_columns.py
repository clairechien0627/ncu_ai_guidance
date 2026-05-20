"""documents: drop deprecated extraction columns (now in document_extractions)

Revision ID: 026
Revises: 025
Create Date: 2026-05-20
"""

from alembic import op

revision = "026"
down_revision = "025"
branch_labels = None
depends_on = None

_DEPRECATED_COLS = [
    "summary_json",
    "category",
    "tags",
    "langsmith_run_id",
    "raw_research_answer",
    "raw_research_sources",
    "raw_research_run_id",
]


def upgrade() -> None:
    with op.batch_alter_table("documents") as batch_op:
        # Drop index created in legacy migration before dropping the column
        try:
            batch_op.drop_index("idx_documents_raw_research_run_id")
        except Exception:
            pass
        for col in _DEPRECATED_COLS:
            try:
                batch_op.drop_column(col)
            except Exception:
                pass


def downgrade() -> None:
    import sqlalchemy as sa
    with op.batch_alter_table("documents") as batch_op:
        batch_op.add_column(sa.Column("summary_json", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("category", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("tags", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("langsmith_run_id", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("raw_research_answer", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("raw_research_sources", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("raw_research_run_id", sa.String(), nullable=True))
