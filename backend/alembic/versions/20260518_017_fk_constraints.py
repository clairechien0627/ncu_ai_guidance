"""Add FK constraints (observations→traces_v2, scores→traces_v2/observations) and Score CHECK constraint.

Revision ID: 017
Revises: 016
Create Date: 2026-05-18
"""
import sqlalchemy as sa
from alembic import op

revision = "017"
down_revision = "016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    # ── observations.trace_id → traces_v2.trace_id (CASCADE DELETE) ──────────
    # First clean up any orphaned observations (trace_id not in traces_v2)
    conn.execute(sa.text("""
        DELETE FROM observations
        WHERE trace_id NOT IN (SELECT trace_id FROM traces_v2)
    """))

    op.create_foreign_key(
        "fk_observations_trace_id",
        "observations", "traces_v2",
        ["trace_id"], ["trace_id"],
        ondelete="CASCADE",
    )

    # ── observations self-referential FK (parent_observation_id) ─────────────
    op.create_foreign_key(
        "fk_observations_parent_observation_id",
        "observations", "observations",
        ["parent_observation_id"], ["observation_id"],
        ondelete="SET NULL",
    )

    # ── scores.trace_id → traces_v2.trace_id (CASCADE DELETE) ────────────────
    # Clean up orphaned scores first
    conn.execute(sa.text("""
        DELETE FROM scores
        WHERE trace_id IS NOT NULL
          AND trace_id NOT IN (SELECT trace_id FROM traces_v2)
    """))

    op.create_foreign_key(
        "fk_scores_trace_id",
        "scores", "traces_v2",
        ["trace_id"], ["trace_id"],
        ondelete="CASCADE",
    )

    # ── scores.observation_id → observations.observation_id (CASCADE DELETE) ─
    conn.execute(sa.text("""
        DELETE FROM scores
        WHERE observation_id IS NOT NULL
          AND observation_id NOT IN (SELECT observation_id FROM observations)
    """))

    op.create_foreign_key(
        "fk_scores_observation_id",
        "scores", "observations",
        ["observation_id"], ["observation_id"],
        ondelete="CASCADE",
    )

    # ── Score CHECK: trace_id OR observation_id must be non-null ─────────────
    op.create_check_constraint(
        "chk_score_linked",
        "scores",
        "trace_id IS NOT NULL OR observation_id IS NOT NULL",
    )


def downgrade() -> None:
    op.drop_constraint("chk_score_linked", "scores", type_="check")
    op.drop_constraint("fk_scores_observation_id", "scores", type_="foreignkey")
    op.drop_constraint("fk_scores_trace_id", "scores", type_="foreignkey")
    op.drop_constraint("fk_observations_parent_observation_id", "observations", type_="foreignkey")
    op.drop_constraint("fk_observations_trace_id", "observations", type_="foreignkey")
