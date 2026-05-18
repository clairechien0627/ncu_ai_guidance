"""Add datasets and dataset evaluation links.

Revision ID: 014
Revises: 013
Create Date: 2026-05-18
"""
import sqlalchemy as sa
from alembic import op

revision = "014"
down_revision = "013"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS datasets (
            id SERIAL PRIMARY KEY,
            dataset_id VARCHAR NOT NULL UNIQUE,
            name VARCHAR NOT NULL,
            description TEXT,
            source VARCHAR,
            metadata JSONB,
            is_archived BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW()
        )
    """))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_datasets_dataset_id ON datasets (dataset_id)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_datasets_name ON datasets (name)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_datasets_source ON datasets (source)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_datasets_is_archived ON datasets (is_archived)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_datasets_created_at ON datasets (created_at DESC)"))

    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS dataset_items (
            id SERIAL PRIMARY KEY,
            dataset_item_id VARCHAR NOT NULL UNIQUE,
            dataset_id VARCHAR NOT NULL REFERENCES datasets(dataset_id) ON DELETE CASCADE,
            input JSONB,
            output JSONB,
            expected_output JSONB,
            context JSONB,
            source_trace_id VARCHAR,
            tags JSONB,
            metadata JSONB,
            is_archived BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW(),
            CONSTRAINT uq_dataset_items_dataset_trace UNIQUE (dataset_id, source_trace_id)
        )
    """))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_dataset_items_dataset_item_id ON dataset_items (dataset_item_id)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_dataset_items_dataset_id ON dataset_items (dataset_id)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_dataset_items_source_trace_id ON dataset_items (source_trace_id)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_dataset_items_is_archived ON dataset_items (is_archived)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_dataset_items_created_at ON dataset_items (created_at DESC)"))

    conn.execute(sa.text("ALTER TABLE evaluation_runs ADD COLUMN IF NOT EXISTS dataset_id VARCHAR"))
    conn.execute(sa.text("ALTER TABLE evaluation_runs ADD COLUMN IF NOT EXISTS dataset_item_count INTEGER NOT NULL DEFAULT 0"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_evaluation_runs_dataset_id ON evaluation_runs (dataset_id)"))
    conn.execute(sa.text("ALTER TABLE evaluation_run_items ADD COLUMN IF NOT EXISTS dataset_item_id VARCHAR"))
    conn.execute(sa.text("ALTER TABLE evaluation_run_items ALTER COLUMN trace_id DROP NOT NULL"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_evaluation_run_items_dataset_item_id ON evaluation_run_items (dataset_item_id)"))


def downgrade():
    conn = op.get_bind()
    conn.execute(sa.text("ALTER TABLE evaluation_run_items DROP COLUMN IF EXISTS dataset_item_id"))
    conn.execute(sa.text("ALTER TABLE evaluation_runs DROP COLUMN IF EXISTS dataset_item_count"))
    conn.execute(sa.text("ALTER TABLE evaluation_runs DROP COLUMN IF EXISTS dataset_id"))
    conn.execute(sa.text("DROP TABLE IF EXISTS dataset_items"))
    conn.execute(sa.text("DROP TABLE IF EXISTS datasets"))
