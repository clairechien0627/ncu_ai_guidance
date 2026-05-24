"""Clean legacy TraceV2 root metadata keys.

Revision ID: 038
Revises: 037
Create Date: 2026-05-24
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "038"
down_revision = "037"
branch_labels = None
depends_on = None


DROP_KEYS = (
    "display",
    "tool_count",
    "llm_call_count",
    "prompt_name",
    "prompt_version",
    "base_prompt_name",
    "task_prompt_name",
    "quality_prompt_name",
    "base_prompt_hash",
    "task_prompt_hash",
    "quality_prompt_hash",
    "prompt_stack_name",
    "prompt_stack_json",
    "primary_prompt_json",
    "workflow_prompts_json",
    "prompt_stack_tokens",
    "research_effective_base_prompt_stack_json",
    "research_effective_system_prompt_json",
    "research_runtime_prompt_json",
    "research_runtime_prompt_summary",
    "extract_step2_prompt_stack_json",
    "extract_step3_prompt_stack_json",
    "extract_step4_prompt_stack_json",
)


def upgrade() -> None:
    conn = op.get_bind()
    keys_sql = ", ".join(f"'{key}'" for key in DROP_KEYS)
    conn.execute(sa.text(f"""
        UPDATE traces_v2
        SET metadata = metadata - ARRAY[{keys_sql}]
        WHERE metadata IS NOT NULL
          AND metadata ?| ARRAY[{keys_sql}]
    """))


def downgrade() -> None:
    # Removed metadata keys were derived/legacy view data and cannot be restored.
    pass
