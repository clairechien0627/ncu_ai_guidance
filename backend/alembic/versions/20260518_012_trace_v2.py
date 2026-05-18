"""Add normalized trace v2 tables.

Revision ID: 012
Revises: 011
Create Date: 2026-05-18
"""
import sqlalchemy as sa
from alembic import op

revision = "012"
down_revision = "011"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS trace_events_outbox (
            id SERIAL PRIMARY KEY,
            event_id VARCHAR NOT NULL UNIQUE,
            event_type VARCHAR NOT NULL,
            body_json JSONB NOT NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'pending',
            attempts INTEGER NOT NULL DEFAULT 0,
            last_error TEXT,
            created_at TIMESTAMP DEFAULT NOW(),
            processed_at TIMESTAMP
        )
    """))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_trace_events_outbox_event_id ON trace_events_outbox (event_id)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_trace_events_outbox_status ON trace_events_outbox (status, created_at)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_trace_events_outbox_event_type ON trace_events_outbox (event_type)"))

    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS traces_v2 (
            id SERIAL PRIMARY KEY,
            trace_id VARCHAR NOT NULL UNIQUE,
            name VARCHAR NOT NULL,
            thread_id VARCHAR,
            session_id VARCHAR,
            user_id VARCHAR,
            environment VARCHAR(40) NOT NULL DEFAULT 'default',
            input JSONB,
            output JSONB,
            metadata JSONB,
            tags JSONB,
            start_time TIMESTAMP NOT NULL,
            end_time TIMESTAMP,
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW()
        )
    """))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_traces_v2_trace_id ON traces_v2 (trace_id)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_traces_v2_thread_id ON traces_v2 (thread_id)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_traces_v2_session_id ON traces_v2 (session_id)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_traces_v2_user_id ON traces_v2 (user_id)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_traces_v2_environment ON traces_v2 (environment)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_traces_v2_start_time ON traces_v2 (start_time DESC)"))

    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS observations (
            id SERIAL PRIMARY KEY,
            observation_id VARCHAR NOT NULL UNIQUE,
            trace_id VARCHAR NOT NULL,
            parent_observation_id VARCHAR,
            type VARCHAR NOT NULL,
            name VARCHAR NOT NULL,
            model VARCHAR,
            usage JSONB,
            cost JSONB,
            prompt_name VARCHAR,
            prompt_version VARCHAR,
            input JSONB,
            output JSONB,
            metadata JSONB,
            level VARCHAR(20) NOT NULL DEFAULT 'DEFAULT',
            status_message TEXT,
            start_time TIMESTAMP NOT NULL,
            end_time TIMESTAMP,
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW()
        )
    """))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_observations_observation_id ON observations (observation_id)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_observations_trace_id ON observations (trace_id, start_time)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_observations_parent_observation_id ON observations (parent_observation_id)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_observations_type ON observations (type)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_observations_prompt ON observations (prompt_name, prompt_version)"))

    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS score_configs (
            id SERIAL PRIMARY KEY,
            name VARCHAR NOT NULL UNIQUE,
            data_type VARCHAR NOT NULL DEFAULT 'NUMERIC',
            min_value DOUBLE PRECISION,
            max_value DOUBLE PRECISION,
            categories JSONB,
            description TEXT,
            is_archived BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW()
        )
    """))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_score_configs_name ON score_configs (name)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_score_configs_data_type ON score_configs (data_type)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_score_configs_is_archived ON score_configs (is_archived)"))

    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS scores (
            id SERIAL PRIMARY KEY,
            score_id VARCHAR NOT NULL UNIQUE,
            trace_id VARCHAR,
            observation_id VARCHAR,
            name VARCHAR NOT NULL,
            value DOUBLE PRECISION,
            string_value TEXT,
            data_type VARCHAR NOT NULL DEFAULT 'NUMERIC',
            source VARCHAR NOT NULL DEFAULT 'API',
            comment TEXT,
            metadata JSONB,
            execution_trace_id VARCHAR,
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW()
        )
    """))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_scores_score_id ON scores (score_id)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_scores_trace_id ON scores (trace_id)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_scores_observation_id ON scores (observation_id)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_scores_name ON scores (name)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_scores_source ON scores (source)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_scores_created_at ON scores (created_at DESC)"))


def downgrade():
    conn = op.get_bind()
    conn.execute(sa.text("DROP TABLE IF EXISTS scores"))
    conn.execute(sa.text("DROP TABLE IF EXISTS score_configs"))
    conn.execute(sa.text("DROP TABLE IF EXISTS observations"))
    conn.execute(sa.text("DROP TABLE IF EXISTS traces_v2"))
    conn.execute(sa.text("DROP TABLE IF EXISTS trace_events_outbox"))
