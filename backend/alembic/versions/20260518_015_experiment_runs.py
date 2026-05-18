"""Add experiment replay runs.

Revision ID: 015
Revises: 014
Create Date: 2026-05-18
"""
import sqlalchemy as sa
from alembic import op

revision = "015"
down_revision = "014"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS experiment_runs (
            id SERIAL PRIMARY KEY,
            experiment_run_id VARCHAR NOT NULL UNIQUE,
            dataset_id VARCHAR NOT NULL REFERENCES datasets(dataset_id) ON DELETE CASCADE,
            name VARCHAR NOT NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'pending',
            target_agent VARCHAR,
            model VARCHAR,
            prompt_name VARCHAR,
            prompt_version VARCHAR,
            runtime_config JSONB,
            metadata JSONB,
            total_count INTEGER NOT NULL DEFAULT 0,
            succeeded_count INTEGER NOT NULL DEFAULT 0,
            failed_count INTEGER NOT NULL DEFAULT 0,
            last_error TEXT,
            started_at TIMESTAMP,
            completed_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW()
        )
    """))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_experiment_runs_experiment_run_id ON experiment_runs (experiment_run_id)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_experiment_runs_dataset_id ON experiment_runs (dataset_id)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_experiment_runs_name ON experiment_runs (name)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_experiment_runs_status ON experiment_runs (status)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_experiment_runs_target_agent ON experiment_runs (target_agent)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_experiment_runs_model ON experiment_runs (model)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_experiment_runs_prompt_name ON experiment_runs (prompt_name)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_experiment_runs_created_at ON experiment_runs (created_at DESC)"))

    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS experiment_run_items (
            id SERIAL PRIMARY KEY,
            experiment_item_id VARCHAR NOT NULL UNIQUE,
            experiment_run_id VARCHAR NOT NULL REFERENCES experiment_runs(experiment_run_id) ON DELETE CASCADE,
            dataset_item_id VARCHAR NOT NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'pending',
            generated_output JSONB,
            generated_context JSONB,
            trace_id VARCHAR,
            eval_run_id VARCHAR,
            error TEXT,
            started_at TIMESTAMP,
            completed_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW(),
            CONSTRAINT uq_experiment_run_items_run_dataset_item UNIQUE (experiment_run_id, dataset_item_id)
        )
    """))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_experiment_run_items_experiment_item_id ON experiment_run_items (experiment_item_id)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_experiment_run_items_experiment_run_id ON experiment_run_items (experiment_run_id)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_experiment_run_items_dataset_item_id ON experiment_run_items (dataset_item_id)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_experiment_run_items_status ON experiment_run_items (status)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_experiment_run_items_trace_id ON experiment_run_items (trace_id)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_experiment_run_items_eval_run_id ON experiment_run_items (eval_run_id)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_experiment_run_items_created_at ON experiment_run_items (created_at DESC)"))


def downgrade():
    conn = op.get_bind()
    conn.execute(sa.text("DROP TABLE IF EXISTS experiment_run_items"))
    conn.execute(sa.text("DROP TABLE IF EXISTS experiment_runs"))
