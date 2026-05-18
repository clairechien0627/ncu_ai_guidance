"""Add evaluation run tracking tables.

Revision ID: 013
Revises: 012
Create Date: 2026-05-18
"""
import sqlalchemy as sa
from alembic import op

revision = "013"
down_revision = "012"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS evaluation_runs (
            id SERIAL PRIMARY KEY,
            eval_run_id VARCHAR NOT NULL UNIQUE,
            name VARCHAR NOT NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'pending',
            scope VARCHAR(40) NOT NULL,
            target_trace_ids JSONB,
            model VARCHAR,
            prompt_name VARCHAR,
            prompt_version VARCHAR,
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
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_evaluation_runs_eval_run_id ON evaluation_runs (eval_run_id)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_evaluation_runs_name ON evaluation_runs (name)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_evaluation_runs_status ON evaluation_runs (status)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_evaluation_runs_scope ON evaluation_runs (scope)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_evaluation_runs_prompt_name ON evaluation_runs (prompt_name)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_evaluation_runs_created_at ON evaluation_runs (created_at DESC)"))

    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS evaluation_run_items (
            id SERIAL PRIMARY KEY,
            eval_item_id VARCHAR NOT NULL UNIQUE,
            eval_run_id VARCHAR NOT NULL REFERENCES evaluation_runs(eval_run_id) ON DELETE CASCADE,
            trace_id VARCHAR NOT NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'pending',
            score_ids JSONB,
            error TEXT,
            started_at TIMESTAMP,
            completed_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW(),
            CONSTRAINT uq_eval_run_items_run_trace UNIQUE (eval_run_id, trace_id)
        )
    """))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_evaluation_run_items_eval_item_id ON evaluation_run_items (eval_item_id)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_evaluation_run_items_eval_run_id ON evaluation_run_items (eval_run_id)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_evaluation_run_items_trace_id ON evaluation_run_items (trace_id)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_evaluation_run_items_status ON evaluation_run_items (status)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_evaluation_run_items_created_at ON evaluation_run_items (created_at DESC)"))


def downgrade():
    conn = op.get_bind()
    conn.execute(sa.text("DROP TABLE IF EXISTS evaluation_run_items"))
    conn.execute(sa.text("DROP TABLE IF EXISTS evaluation_runs"))
