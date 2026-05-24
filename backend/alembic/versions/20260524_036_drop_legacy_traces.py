"""Drop legacy traces table.

Revision ID: 036
Revises: 035
Create Date: 2026-05-24

Downgrade recreates the old table shape only; dropped trace data is not restored.
"""
import sqlalchemy as sa
from alembic import op

revision = "036"
down_revision = "035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("DROP TABLE IF EXISTS traces CASCADE"))


def downgrade() -> None:
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS traces (
            id SERIAL PRIMARY KEY,
            observation_id VARCHAR NOT NULL UNIQUE,
            trace_id VARCHAR,
            run_type VARCHAR NOT NULL,
            name VARCHAR NOT NULL,
            inputs JSONB,
            outputs JSONB,
            error TEXT,
            start_time TIMESTAMP,
            end_time TIMESTAMP,
            prompt_tokens INTEGER,
            completion_tokens INTEGER,
            thread_id VARCHAR,
            document_ids JSONB,
            agent_name VARCHAR,
            prompt_name VARCHAR,
            prompt_version VARCHAR,
            base_prompt_name VARCHAR,
            task_prompt_name VARCHAR,
            quality_prompt_name VARCHAR,
            base_prompt_hash VARCHAR,
            task_prompt_hash VARCHAR,
            quality_prompt_hash VARCHAR,
            prompt_stack_name VARCHAR,
            prompt_stack_json JSONB,
            primary_prompt_json JSONB,
            workflow_prompts_json JSONB,
            prompt_stack_tokens INTEGER,
            tool_count INTEGER,
            llm_call_count INTEGER,
            quality_score DOUBLE PRECISION,
            user_feedback TEXT,
            display JSONB,
            quality_detail JSONB,
            environment VARCHAR(40) NOT NULL DEFAULT 'default',
            user_id VARCHAR
        )
    """))
    for name, column in [
        ("ix_traces_observation_id", "observation_id"),
        ("ix_traces_trace_id", "trace_id"),
        ("ix_traces_thread_id", "thread_id"),
        ("ix_traces_agent_name", "agent_name"),
        ("ix_traces_prompt_name", "prompt_name"),
        ("ix_traces_environment", "environment"),
        ("ix_traces_user_id", "user_id"),
    ]:
        op.execute(sa.text(f"CREATE INDEX IF NOT EXISTS {name} ON traces ({column})"))
