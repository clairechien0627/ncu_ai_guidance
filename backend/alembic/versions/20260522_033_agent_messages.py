"""agent_messages: lightweight Q&A log to decouple evaluation_agent from Trace table

Revision ID: 033
Revises: 032
Create Date: 2026-05-22
"""

from alembic import op
import sqlalchemy as sa

revision = "033"
down_revision = "032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS agent_messages (
            id SERIAL PRIMARY KEY,
            message_id VARCHAR(36) NOT NULL,
            thread_id VARCHAR NOT NULL,
            user_id VARCHAR,
            agent_name VARCHAR NOT NULL,
            user_question TEXT,
            agent_answer TEXT,
            sources JSONB,
            trace_summary JSONB,
            observation_id VARCHAR,
            created_at TIMESTAMPTZ DEFAULT NOW()
        );
        CREATE UNIQUE INDEX IF NOT EXISTS ix_agent_messages_message_id ON agent_messages(message_id);
        CREATE INDEX IF NOT EXISTS ix_agent_messages_thread_id ON agent_messages(thread_id);
        CREATE INDEX IF NOT EXISTS ix_agent_messages_user_id ON agent_messages(user_id);
        CREATE INDEX IF NOT EXISTS ix_agent_messages_agent_name ON agent_messages(agent_name);
        CREATE INDEX IF NOT EXISTS ix_agent_messages_observation_id ON agent_messages(observation_id);
        CREATE INDEX IF NOT EXISTS ix_agent_messages_created_at ON agent_messages(created_at DESC);
    """))


def downgrade() -> None:
    op.execute(sa.text("DROP TABLE IF EXISTS agent_messages;"))
