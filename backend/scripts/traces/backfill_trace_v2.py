"""Backfill legacy traces into Trace System v2 tables.

Manual usage:
    python backend/scripts/traces/backfill_trace_v2.py --dry-run --limit 100
    python backend/scripts/traces/backfill_trace_v2.py --run-id <trace_id>
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from db import Observation, Score, Trace, TraceV2
from db.session import SessionLocal
from services.trace_repositories import ObservationRepository, ScoreRepository, TraceRepository


QUALITY_DIMENSIONS = {
    "overall",
    "grounding",
    "task_fit",
    "completeness",
    "specificity",
    "source_quality",
    "uncertainty_honesty",
    "format_fit",
}


def _agent_execution_root():
    # Router runs ARE the roots; task/chain runs under them become observations.
    return Trace.agent_name == "router_agent"


def _parse_json(value: Any) -> Any:
    if value is None or isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return value


def _parse_since(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)


def _trace_metadata(trace: Trace) -> dict:
    return {
        "task_type": trace.task_type,
        "route_intent": trace.route_intent,
        "agent_name": trace.agent_name,
        "prompt_name": trace.prompt_name,
        "prompt_version": trace.prompt_version,
        "base_prompt_name": trace.base_prompt_name,
        "task_prompt_name": trace.task_prompt_name,
        "quality_prompt_name": trace.quality_prompt_name,
        "base_prompt_hash": trace.base_prompt_hash,
        "task_prompt_hash": trace.task_prompt_hash,
        "quality_prompt_hash": trace.quality_prompt_hash,
        "prompt_stack_name": trace.prompt_stack_name,
        "prompt_stack_json": _parse_json(trace.prompt_stack_json),
        "primary_prompt_json": _parse_json(trace.primary_prompt_json),
        "workflow_prompts_json": _parse_json(trace.workflow_prompts_json),
        "prompt_stack_tokens": trace.prompt_stack_tokens,
        "tool_count": trace.tool_count,
        "llm_call_count": trace.llm_call_count,
        "original_intent": trace.original_intent,
        "resolved_intent": trace.resolved_intent,
        "document_ids": _parse_json(trace.document_ids),
        "display": _parse_json(trace.display),
        "legacy_run_type": trace.run_type,
        "legacy_trace_id": trace.trace_id,
        "source": "legacy_backfill",
    }


def _trace_body(trace: Trace) -> dict:
    tags = [
        value
        for value in [trace.route_intent, trace.task_type, trace.agent_name]
        if value
    ]
    return {
        "trace_id": trace.observation_id,
        "name": trace.name,
        "thread_id": trace.thread_id,
        "user_id": trace.user_id,
        "environment": trace.environment or "default",
        "input": _parse_json(trace.inputs),
        "output": _parse_json(trace.outputs),
        "metadata": _trace_metadata(trace),
        "tags": tags,
        "start_time": trace.start_time,
        "end_time": trace.end_time,
    }


def _observation_body(root: Trace, child: Trace) -> dict:
    usage = {
        "input": child.prompt_tokens or 0,
        "output": child.completion_tokens or 0,
        "total": (child.prompt_tokens or 0) + (child.completion_tokens or 0),
        "unit": "TOKENS",
    }
    return {
        "observation_id": child.observation_id,
        "trace_id": root.observation_id,
        "parent_observation_id": child.trace_id if child.trace_id != root.observation_id else None,
        "type": child.run_type or "span",
        "name": child.name,
        "usage": usage,
        "prompt_name": child.prompt_name,
        "prompt_version": child.prompt_version,
        "input": _parse_json(child.inputs),
        "output": _parse_json(child.outputs),
        "metadata": _trace_metadata(child),
        "level": "ERROR" if child.error else "DEFAULT",
        "status_message": child.error,
        "start_time": child.start_time,
        "end_time": child.end_time,
    }


def _score_bodies(trace: Trace) -> list[dict]:
    bodies: dict[str, dict] = {}

    detail = _parse_json(trace.quality_detail)
    if isinstance(detail, dict):
        for name, value in detail.items():
            if name in QUALITY_DIMENSIONS and isinstance(value, (int, float)):
                bodies[f"{trace.observation_id}:{name}:legacy-backfill"] = {
                    "score_id": f"{trace.observation_id}:{name}:legacy-backfill",
                    "trace_id": trace.observation_id,
                    "name": name,
                    "value": float(value),
                    "data_type": "NUMERIC",
                    "source": "EVAL",
                    "comment": trace.user_feedback,
                    "metadata": {"source": "legacy_backfill"},
                    "execution_trace_id": trace.observation_id,
                }
            elif name == "verdict" and isinstance(value, str):
                bodies[f"{trace.observation_id}:verdict:legacy-backfill"] = {
                    "score_id": f"{trace.observation_id}:verdict:legacy-backfill",
                    "trace_id": trace.observation_id,
                    "name": "verdict",
                    "string_value": value,
                    "data_type": "CATEGORICAL",
                    "source": "EVAL",
                    "metadata": {"source": "legacy_backfill"},
                    "execution_trace_id": trace.observation_id,
                }

    if trace.quality_score is not None:
        bodies[f"{trace.observation_id}:overall:legacy-backfill"] = {
            "score_id": f"{trace.observation_id}:overall:legacy-backfill",
            "trace_id": trace.observation_id,
            "name": "overall",
            "value": float(trace.quality_score),
            "data_type": "NUMERIC",
            "source": "EVAL",
            "comment": trace.user_feedback,
            "metadata": {"source": "legacy_backfill"},
            "execution_trace_id": trace.observation_id,
        }

    if trace.user_feedback:
        bodies[f"{trace.observation_id}:feedback:legacy-backfill"] = {
            "score_id": f"{trace.observation_id}:feedback:legacy-backfill",
            "trace_id": trace.observation_id,
            "name": "feedback",
            "string_value": trace.user_feedback,
            "data_type": "TEXT",
            "source": "ANNOTATION",
            "comment": trace.user_feedback,
            "metadata": {"source": "legacy_backfill"},
        }

    return list(bodies.values())


def _root_query(db: Session, *, run_id: str | None, since: datetime | None):
    q = db.query(Trace).filter(_agent_execution_root())
    if run_id:
        q = q.filter(Trace.observation_id == run_id)
    if since:
        q = q.filter(Trace.start_time >= since)
    return q.order_by(Trace.start_time.asc())


def backfill(db: Session, *, dry_run: bool = False, limit: int | None = None, run_id: str | None = None, since: datetime | None = None) -> dict:
    q = _root_query(db, run_id=run_id, since=since)
    if limit is not None:
        q = q.limit(limit)
    roots = q.all()

    summary = {
        "dry_run": dry_run,
        "roots_seen": len(roots),
        "traces_written": 0,
        "observations_written": 0,
        "scores_written": 0,
    }

    for root in roots:
        # Collect ALL descendants (not just direct children) so deeply nested
        # task runs also become observations under the router trace.
        descendants: list[Trace] = []
        queue = [root.observation_id]
        while queue:
            parent_id = queue.pop()
            children = (
                db.query(Trace)
                .filter(Trace.trace_id == parent_id)
                .order_by(Trace.start_time.asc())
                .all()
            )
            descendants.extend(children)
            queue.extend(c.run_id for c in children)

        score_bodies = _score_bodies(root)

        if dry_run:
            summary["traces_written"] += 1
            summary["observations_written"] += len(descendants)
            summary["scores_written"] += len(score_bodies)
            continue

        TraceRepository.upsert_trace(db, _trace_body(root))
        summary["traces_written"] += 1
        for child in descendants:
            ObservationRepository.upsert_observation(db, _observation_body(root, child))
            summary["observations_written"] += 1
        for score in score_bodies:
            try:
                ScoreRepository.upsert_score(db, score, sync_legacy_cache=False)
                summary["scores_written"] += 1
            except ValueError:
                summary.setdefault("scores_skipped", 0)
                summary["scores_skipped"] += 1

    if dry_run:
        db.rollback()
    else:
        db.commit()
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill legacy traces into normalized v2 trace tables.")
    parser.add_argument("--dry-run", action="store_true", help="Count planned writes without changing the database.")
    parser.add_argument("--limit", type=int, default=None, help="Maximum number of root traces to process.")
    parser.add_argument("--run-id", default=None, help="Backfill one root trace by run_id.")
    parser.add_argument("--since", default=None, help="Only backfill root traces whose start_time is at or after this ISO timestamp.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    db = SessionLocal()
    try:
        summary = backfill(
            db,
            dry_run=args.dry_run,
            limit=args.limit,
            run_id=args.run_id,
            since=_parse_since(args.since),
        )
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
