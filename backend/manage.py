#!/usr/bin/env python
"""Admin CLI for Report Agent backend.

Usage:
    python manage.py migrate                  # Apply pending Alembic migrations
    python manage.py reset-stuck             # Reset documents/jobs stuck in processing state
    python manage.py clear-traces [--days N] # Delete TraceV2 rows older than N days (default: 30)
    python manage.py shell                   # Drop into an interactive Python REPL with app context
"""
import argparse
import os
import sys

from dotenv import load_dotenv

load_dotenv()

if sys.platform == "win32":
    import asyncio
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def cmd_migrate(args) -> None:
    """Apply all pending Alembic migrations."""
    import logging
    from alembic.config import Config as AlembicConfig
    from alembic import command as alembic_command

    logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
    cfg = AlembicConfig(os.path.join(os.path.dirname(__file__), "alembic.ini"))
    alembic_command.upgrade(cfg, "head")
    print("✓ Migrations applied.")


def cmd_reset_stuck(args) -> None:
    """Reset documents and jobs that are stuck in 'processing' state after a crash."""
    from db import create_tables, db_session, Document
    from sqlalchemy import update

    create_tables()
    with db_session() as db:
        docs = db.query(Document).filter(Document.batch_status == "processing").all()
        for doc in docs:
            doc.batch_status = "summarized" if doc.summary_json else "pending"

        reindex_stuck = db.query(Document).filter(Document.status == "processing").all()
        for doc in reindex_stuck:
            doc.status = "error"

        db.commit()

    print(f"✓ Reset {len(docs)} extraction-stuck document(s), {len(reindex_stuck)} reindex-stuck document(s).")


def cmd_clear_traces(args) -> None:
    """Delete traces older than --days days (default 30)."""
    from datetime import datetime, timedelta, timezone
    from db import create_tables, db_session, TraceV2, Observation, Score

    create_tables()
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=args.days)
    with db_session() as db:
        trace_ids = [
            row[0]
            for row in db.query(TraceV2.trace_id)
            .filter(TraceV2.start_time < cutoff)
            .all()
        ]
        deleted = 0
        if trace_ids:
            db.query(Score).filter(Score.trace_id.in_(trace_ids)).delete(synchronize_session=False)
            db.query(Observation).filter(Observation.trace_id.in_(trace_ids)).delete(synchronize_session=False)
            deleted = db.query(TraceV2).filter(TraceV2.trace_id.in_(trace_ids)).delete(synchronize_session=False)
        db.commit()
    print(f"✓ Deleted {deleted} trace(s) older than {args.days} day(s) (before {cutoff.date()}).")


def cmd_shell(args) -> None:
    """Interactive REPL with db_session, Document, TraceV2 pre-imported."""
    import code
    from db import db_session, Document, TraceV2, Observation, Score, JobHistory  # noqa: F401

    banner = (
        "Report Agent management shell\n"
        "Available: db_session, Document, TraceV2, Observation, Score, JobHistory\n"
        "Example:   with db_session() as db: print(db.query(Document).count())\n"
    )
    code.interact(banner=banner, local=locals())


def main() -> None:
    parser = argparse.ArgumentParser(description="Report Agent management CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("migrate", help="Apply pending Alembic migrations")
    sub.add_parser("reset-stuck", help="Reset documents/jobs stuck in processing state")

    p_clear = sub.add_parser("clear-traces", help="Delete traces older than N days")
    p_clear.add_argument("--days", type=int, default=30, metavar="N", help="Retention period in days (default: 30)")

    sub.add_parser("shell", help="Interactive REPL with app context")

    args = parser.parse_args()
    dispatch = {
        "migrate":      cmd_migrate,
        "reset-stuck":  cmd_reset_stuck,
        "clear-traces": cmd_clear_traces,
        "shell":        cmd_shell,
    }
    dispatch[args.command](args)


if __name__ == "__main__":
    main()
