"""Harden trace v2 score, dataset, observation, and outbox models.

Revision ID: 016
Revises: 015
Create Date: 2026-05-18
"""
import sqlalchemy as sa
from alembic import op

revision = "016"
down_revision = "015"
branch_labels = None
depends_on = None


def _add_column_if_missing(conn, table: str, column: str, ddl: str) -> None:
    exists = conn.execute(sa.text("""
        SELECT 1
        FROM information_schema.columns
        WHERE table_name = :table AND column_name = :column
    """), {"table": table, "column": column}).first()
    if not exists:
        conn.execute(sa.text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))


def upgrade():
    conn = op.get_bind()

    _add_column_if_missing(conn, "trace_events_outbox", "locked_at", "TIMESTAMP")
    _add_column_if_missing(conn, "trace_events_outbox", "locked_by", "VARCHAR")
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_trace_events_outbox_locked_at ON trace_events_outbox (locked_at)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_trace_events_outbox_locked_by ON trace_events_outbox (locked_by)"))

    for column, ddl in [
        ("model_parameters", "JSONB"),
        ("prompt_tokens", "INTEGER"),
        ("completion_tokens", "INTEGER"),
        ("total_tokens", "INTEGER"),
        ("input_cost", "DOUBLE PRECISION"),
        ("output_cost", "DOUBLE PRECISION"),
        ("total_cost", "DOUBLE PRECISION"),
        ("completion_start_time", "TIMESTAMP"),
    ]:
        _add_column_if_missing(conn, "observations", column, ddl)
    for column in ["prompt_tokens", "completion_tokens", "total_tokens", "total_cost", "completion_start_time"]:
        conn.execute(sa.text(f"CREATE INDEX IF NOT EXISTS ix_observations_{column} ON observations ({column})"))

    for column, ddl in [
        ("eval_run_id", "VARCHAR"),
        ("eval_item_id", "VARCHAR"),
        ("dataset_id", "VARCHAR"),
        ("dataset_item_id", "VARCHAR"),
        ("experiment_run_id", "VARCHAR"),
        ("experiment_item_id", "VARCHAR"),
        ("score_config_id", "INTEGER REFERENCES score_configs(id)"),
        ("timestamp", "TIMESTAMP"),
    ]:
        _add_column_if_missing(conn, "scores", column, ddl)
    for column in [
        "eval_run_id",
        "eval_item_id",
        "dataset_id",
        "dataset_item_id",
        "experiment_run_id",
        "experiment_item_id",
        "score_config_id",
        "timestamp",
    ]:
        conn.execute(sa.text(f"CREATE INDEX IF NOT EXISTS ix_scores_{column} ON scores ({column})"))

    conn.execute(sa.text("""
        UPDATE scores
        SET
            eval_run_id = COALESCE(eval_run_id, metadata->>'eval_run_id', execution_trace_id),
            eval_item_id = COALESCE(eval_item_id, metadata->>'eval_item_id'),
            dataset_id = COALESCE(dataset_id, metadata->>'dataset_id'),
            dataset_item_id = COALESCE(dataset_item_id, metadata->>'dataset_item_id'),
            experiment_run_id = COALESCE(experiment_run_id, metadata->>'experiment_run_id'),
            experiment_item_id = COALESCE(experiment_item_id, metadata->>'experiment_item_id'),
            timestamp = COALESCE(timestamp, created_at)
        WHERE metadata IS NOT NULL OR execution_trace_id IS NOT NULL
    """))
    conn.execute(sa.text("""
        UPDATE scores s
        SET score_config_id = c.id
        FROM score_configs c
        WHERE s.score_config_id IS NULL AND c.name = s.name
    """))

    _add_column_if_missing(conn, "evaluation_run_items", "experiment_item_id", "VARCHAR")
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_evaluation_run_items_experiment_item_id ON evaluation_run_items (experiment_item_id)"))

    for column, ddl in [
        ("source_observation_id", "VARCHAR"),
        ("status", "VARCHAR(20) NOT NULL DEFAULT 'active'"),
        ("is_deleted", "BOOLEAN NOT NULL DEFAULT FALSE"),
        ("valid_from", "TIMESTAMP"),
        ("valid_to", "TIMESTAMP"),
    ]:
        _add_column_if_missing(conn, "dataset_items", column, ddl)
    for column in ["source_observation_id", "status", "is_deleted", "valid_from", "valid_to"]:
        conn.execute(sa.text(f"CREATE INDEX IF NOT EXISTS ix_dataset_items_{column} ON dataset_items ({column})"))
    conn.execute(sa.text("UPDATE dataset_items SET valid_from = COALESCE(valid_from, created_at), status = COALESCE(status, 'active')"))


def downgrade():
    conn = op.get_bind()
    # Keep downgrade conservative; dropping columns risks data loss in active trace systems.
    conn.execute(sa.text("SELECT 1"))
