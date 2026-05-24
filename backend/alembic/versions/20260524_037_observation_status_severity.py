"""observation status uses severity values

Revision ID: 037
Revises: 036
Create Date: 2026-05-24
"""
from alembic import op
import sqlalchemy as sa


revision = "037"
down_revision = "036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {col["name"] for col in inspector.get_columns("observations")}

    if "status" not in columns:
        op.add_column(
            "observations",
            sa.Column("status", sa.String(length=20), nullable=True),
        )

    if "level" in columns:
        op.execute(
            """
            UPDATE observations
            SET status = CASE
                WHEN UPPER(COALESCE(level, '')) IN ('DEBUG', 'DEFAULT', 'WARNING', 'ERROR')
                    THEN UPPER(level)
                WHEN status_message IS NOT NULL AND status_message != ''
                    THEN 'ERROR'
                ELSE 'DEFAULT'
            END
            """
        )
        op.drop_column("observations", "level")
    else:
        op.execute(
            """
            UPDATE observations
            SET status = CASE
                WHEN UPPER(COALESCE(status, '')) IN ('DEBUG', 'DEFAULT', 'WARNING', 'ERROR')
                    THEN UPPER(status)
                WHEN LOWER(COALESCE(status, '')) IN ('error', 'failed', 'failure')
                    THEN 'ERROR'
                WHEN status_message IS NOT NULL AND status_message != ''
                    THEN 'ERROR'
                ELSE 'DEFAULT'
            END
            """
        )

    op.alter_column(
        "observations",
        "status",
        existing_type=sa.String(length=20),
        nullable=False,
        server_default="DEFAULT",
    )


def downgrade() -> None:
    op.add_column(
        "observations",
        sa.Column("level", sa.String(length=20), nullable=True),
    )
    op.execute(
        """
        UPDATE observations
        SET level = CASE
            WHEN UPPER(COALESCE(status, '')) IN ('DEBUG', 'DEFAULT', 'WARNING', 'ERROR')
                THEN UPPER(status)
            ELSE 'DEFAULT'
        END
        """
    )
    op.execute(
        """
        UPDATE observations
        SET status = CASE
            WHEN level = 'ERROR' THEN 'error'
            ELSE 'success'
        END
        """
    )
    op.alter_column(
        "observations",
        "level",
        existing_type=sa.String(length=20),
        nullable=False,
        server_default="DEFAULT",
    )
