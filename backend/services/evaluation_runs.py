"""Persistent evaluation run pipeline for Trace System v2."""
from __future__ import annotations
from ids import new_id

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from db import Dataset, DatasetItem, EvaluationRun, EvaluationRunItem, Trace, db_session
from db.models import Score, TraceV2
from services.trace_repositories import ScoreRepository, TraceRepository


EvaluatorFn = Callable[..., Awaitable[object]]


QUALITY_NUMERIC_FIELDS = [
    "grounding",
    "task_fit",
    "completeness",
    "specificity",
    "source_quality",
    "uncertainty_honesty",
    "format_fit",
    "overall",
]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _json_text(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def _json_obj(value):
    if value is None or isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return value


def _agent_execution_root():
    router_observation_ids = (
        select(Trace.observation_id).where(Trace.agent_name == "router_agent").scalar_subquery()
    )
    return or_(
        Trace.trace_id.in_(router_observation_ids),
        and_(Trace.trace_id.is_(None), Trace.agent_name != "router_agent"),
    )


def _result_to_dict(result) -> dict:
    if isinstance(result, dict):
        return result
    if hasattr(result, "model_dump"):
        return result.model_dump()
    return {
        key: getattr(result, key)
        for key in dir(result)
        if not key.startswith("_") and not callable(getattr(result, key))
    }


def _format_evaluation_comment(result: dict) -> str:
    overall = result.get("overall")
    grounding = result.get("grounding")
    task_fit = result.get("task_fit")
    completeness = result.get("completeness")
    verdict = result.get("verdict") or "未提供 verdict"
    issues = result.get("issues") if isinstance(result.get("issues"), list) else []
    issue_text = "；".join(str(item) for item in issues[:3]) if issues else "未列出明顯問題"
    return (
        f"overall {float(overall or 0):.1f} | grounding {float(grounding or 0):.1f} / "
        f"task_fit {float(task_fit or 0):.1f} / completeness {float(completeness or 0):.1f} | "
        f"{verdict} | {issue_text}"
    )


def _trace_payload(trace: Trace) -> tuple[str, str, list[str], dict, dict]:
    display = _json_obj(trace.display) or {}
    outputs = _json_obj(trace.outputs) or {}
    inputs = _json_obj(trace.inputs) or {}
    answer = str(display.get("answer") or outputs.get("answer") or outputs.get("content") or "")
    sources = display.get("sources") if isinstance(display.get("sources"), list) else []
    trace_summary = display.get("trace_summary") if isinstance(display.get("trace_summary"), dict) else {}
    user_task = ""
    messages = display.get("messages") if isinstance(display.get("messages"), list) else []
    for msg in messages:
        if isinstance(msg, dict) and msg.get("role") in ("human", "user"):
            user_task = str(msg.get("content") or "")
            break
    if not user_task:
        user_task = json.dumps(inputs, ensure_ascii=False)[:1200] if inputs else ""
    extra_context = {"observation_id": trace.observation_id, "agent_name": trace.agent_name}
    return user_task, answer, sources, trace_summary, extra_context


def _dataset_item_payload(item: DatasetItem) -> tuple[str, str, list[str], dict, dict]:
    input_obj = _json_obj(item.input) or {}
    output_obj = _json_obj(item.output) or {}
    expected = _json_obj(item.expected_output)
    context = _json_obj(item.context) or {}
    user_task = ""
    if isinstance(input_obj, dict):
        messages = input_obj.get("messages") if isinstance(input_obj.get("messages"), list) else []
        for msg in messages:
            if isinstance(msg, dict) and msg.get("role") in ("human", "user"):
                user_task = str(msg.get("content") or "")
                break
    if not user_task:
        user_task = json.dumps(input_obj, ensure_ascii=False)[:1200] if input_obj else ""
    answer = ""
    if isinstance(output_obj, dict):
        answer = str(output_obj.get("answer") or output_obj.get("content") or expected or "")
    else:
        answer = str(output_obj or expected or "")
    sources = context.get("sources") if isinstance(context.get("sources"), list) else []
    trace_summary = context.get("trace_summary") if isinstance(context.get("trace_summary"), dict) else {}
    extra_context = {
        "dataset_id": item.dataset_id,
        "dataset_item_id": item.dataset_item_id,
        "source_trace_id": item.source_trace_id,
        "expected_output": expected,
        "context": context,
    }
    return user_task, answer, sources, trace_summary, extra_context


def evaluation_result_to_score_payloads(
    *,
    eval_run_id: str,
    eval_item_id: str,
    trace_id: str,
    result,
    route_intent: str | None = None,
    task_type: str | None = None,
    dataset_id: str | None = None,
    dataset_item_id: str | None = None,
) -> list[dict]:
    detail = _result_to_dict(result)
    comment = _format_evaluation_comment(detail)
    payloads: list[dict] = []
    metadata = {
        "eval_run_id": eval_run_id,
        "eval_item_id": eval_item_id,
        "route_intent": route_intent,
        "task_type": task_type,
        "dataset_id": dataset_id,
        "dataset_item_id": dataset_item_id,
    }
    subject_id = trace_id or dataset_item_id or eval_item_id
    for name in QUALITY_NUMERIC_FIELDS:
        value = detail.get(name)
        if isinstance(value, (int, float)):
            payloads.append({
                "score_id": f"{eval_run_id}:{eval_item_id}:{subject_id}:{name}",
                "trace_id": trace_id,
                "name": name,
                "value": float(value),
                "data_type": "NUMERIC",
                "source": "EVAL",
                "comment": comment,
                "metadata": metadata,
                "execution_trace_id": eval_run_id,
                "eval_run_id": eval_run_id,
                "eval_item_id": eval_item_id,
                "dataset_id": dataset_id,
                "dataset_item_id": dataset_item_id,
            })
    verdict = detail.get("verdict")
    if isinstance(verdict, str) and verdict:
        payloads.append({
                "score_id": f"{eval_run_id}:{eval_item_id}:{subject_id}:verdict",
            "trace_id": trace_id,
            "name": "verdict",
            "string_value": verdict,
            "data_type": "CATEGORICAL",
            "source": "EVAL",
            "comment": comment,
            "metadata": metadata,
            "execution_trace_id": eval_run_id,
            "eval_run_id": eval_run_id,
            "eval_item_id": eval_item_id,
            "dataset_id": dataset_id,
            "dataset_item_id": dataset_item_id,
        })
    return payloads


def _run_payload(row: EvaluationRun, *, include_items: bool = False) -> dict:
    payload = {
        "eval_run_id": row.eval_run_id,
        "name": row.name,
        "status": row.status,
        "scope": row.scope,
        "target_trace_ids": _json_obj(row.target_trace_ids) or [],
        "model": row.model,
        "prompt_name": row.prompt_name,
        "prompt_version": row.prompt_version,
        "dataset_id": row.dataset_id,
        "dataset_item_count": row.dataset_item_count,
        "metadata": _json_obj(row.metadata_json) or {},
        "total_count": row.total_count,
        "succeeded_count": row.succeeded_count,
        "failed_count": row.failed_count,
        "last_error": row.last_error,
        "started_at": row.started_at.isoformat() + "Z" if row.started_at else None,
        "completed_at": row.completed_at.isoformat() + "Z" if row.completed_at else None,
        "created_at": row.created_at.isoformat() + "Z" if row.created_at else None,
        "updated_at": row.updated_at.isoformat() + "Z" if row.updated_at else None,
    }
    if include_items:
        payload["items"] = [_item_payload(item) for item in sorted(row.items, key=lambda item: item.created_at or datetime.min)]
    return payload


def _item_payload(row: EvaluationRunItem) -> dict:
    return {
        "eval_item_id": row.eval_item_id,
        "eval_run_id": row.eval_run_id,
        "trace_id": row.trace_id,
        "dataset_item_id": row.dataset_item_id,
        "status": row.status,
        "score_ids": _json_obj(row.score_ids) or [],
        "error": row.error,
        "started_at": row.started_at.isoformat() + "Z" if row.started_at else None,
        "completed_at": row.completed_at.isoformat() + "Z" if row.completed_at else None,
        "created_at": row.created_at.isoformat() + "Z" if row.created_at else None,
        "updated_at": row.updated_at.isoformat() + "Z" if row.updated_at else None,
    }


class EvaluationRunService:
    @staticmethod
    def create_trace_batch(
        db: Session,
        *,
        limit: int = 50,
        name: str = "trace-batch-score",
        metadata: dict | None = None,
    ) -> EvaluationRun:
        # v2-first: pick from traces_v2 that have no overall score yet
        scored_ids = select(Score.trace_id).where(Score.name == "overall", Score.trace_id.isnot(None))
        v2_traces = (
            db.query(TraceV2)
            .filter(TraceV2.trace_id.notin_(scored_ids))
            .order_by(TraceV2.start_time.desc())
            .limit(limit)
            .all()
        )
        trace_ids = [t.trace_id for t in v2_traces]
        # fallback: legacy traces not already covered by v2 or scored
        if len(trace_ids) < limit:
            already = set(trace_ids)
            legacy = (
                db.query(Trace)
                .filter(
                    _agent_execution_root(),
                    Trace.quality_score.is_(None),
                    Trace.error.is_(None),
                    Trace.observation_id.notin_(already) if already else True,
                )
                .order_by(Trace.start_time.desc())
                .limit(limit - len(trace_ids))
                .all()
            )
            trace_ids += [t.observation_id for t in legacy]
        if not trace_ids:
            return None
        eval_run_id = f"eval-{new_id()}"
        now = _utcnow()
        run = EvaluationRun(
            eval_run_id=eval_run_id,
            name=name,
            status="pending",
            scope="trace_batch",
            target_trace_ids=_json_text(trace_ids),
            prompt_name="evaluation_agent",
            metadata_json=_json_text(metadata or {"source": "batch_score", "limit": limit}),
            total_count=len(trace_ids),
            created_at=now,
            updated_at=now,
        )
        db.add(run)
        for trace_id in trace_ids:
            db.add(EvaluationRunItem(
                eval_item_id=f"{eval_run_id}:{trace_id}",
                eval_run_id=eval_run_id,
                trace_id=trace_id,
                status="pending",
                created_at=now,
                updated_at=now,
            ))
        db.commit()
        db.refresh(run)
        return run

    @staticmethod
    def create_single_trace(
        db: Session,
        *,
        trace_id: str,
        name: str = "background-evaluation",
        scope: str = "single_trace",
        metadata: dict | None = None,
        commit: bool = True,
    ) -> EvaluationRun:
        eval_run_id = f"eval-{new_id()}"
        now = _utcnow()
        run = EvaluationRun(
            eval_run_id=eval_run_id,
            name=name,
            status="pending",
            scope=scope,
            target_trace_ids=_json_text([trace_id]),
            prompt_name="evaluation_agent",
            metadata_json=_json_text(metadata or {"source": name}),
            total_count=1,
            created_at=now,
            updated_at=now,
        )
        db.add(run)
        db.add(EvaluationRunItem(
            eval_item_id=f"{eval_run_id}:{trace_id}",
            eval_run_id=eval_run_id,
            trace_id=trace_id,
            status="pending",
            created_at=now,
            updated_at=now,
        ))
        if commit:
            db.commit()
            db.refresh(run)
        return run

    @staticmethod
    def create_dataset_run(
        db: Session,
        *,
        dataset_id: str,
        name: str = "dataset-evaluation",
        metadata: dict | None = None,
    ) -> EvaluationRun | None:
        dataset = db.query(Dataset).filter(Dataset.dataset_id == dataset_id, Dataset.is_archived.is_(False)).first()
        if dataset is None:
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="Dataset not found")

        items = (
            db.query(DatasetItem)
            .filter(DatasetItem.dataset_id == dataset_id, DatasetItem.is_archived.is_(False))
            .order_by(DatasetItem.created_at.asc())
            .all()
        )
        if not items:
            return None

        eval_run_id = f"eval-{new_id()}"
        now = _utcnow()
        run = EvaluationRun(
            eval_run_id=eval_run_id,
            name=name,
            status="pending",
            scope="dataset",
            dataset_id=dataset_id,
            dataset_item_count=len(items),
            target_trace_ids=_json_text([item.source_trace_id for item in items if item.source_trace_id]),
            prompt_name="evaluation_agent",
            metadata_json=_json_text(metadata or {"source": "dataset_eval", "dataset_id": dataset_id}),
            total_count=len(items),
            created_at=now,
            updated_at=now,
        )
        db.add(run)
        for item in items:
            db.add(EvaluationRunItem(
                eval_item_id=f"{eval_run_id}:{item.dataset_item_id}",
                eval_run_id=eval_run_id,
                trace_id=item.source_trace_id,
                dataset_item_id=item.dataset_item_id,
                status="pending",
                created_at=now,
                updated_at=now,
            ))
        db.commit()
        db.refresh(run)
        return run

    @staticmethod
    async def evaluate_single_trace(
        trace_id: str,
        *,
        evaluator: EvaluatorFn | None = None,
        name: str = "background-evaluation",
        scope: str = "single_trace",
        metadata: dict | None = None,
    ) -> object | None:
        with db_session() as db:
            trace = db.query(Trace).filter(Trace.observation_id == trace_id).first()
            if trace is None:
                return None
            run = EvaluationRunService.create_single_trace(
                db,
                trace_id=trace_id,
                name=name,
                scope=scope,
                metadata=metadata,
                commit=True,
            )
            eval_item_id = f"{run.eval_run_id}:{trace_id}"
        return await EvaluationRunService.evaluate_trace_item(eval_item_id, evaluator=evaluator, return_result=True)

    @staticmethod
    async def process_run(eval_run_id: str, evaluator: EvaluatorFn | None = None) -> None:
        evaluator = evaluator or EvaluationRunService._default_evaluator
        with db_session() as db:
            run = db.query(EvaluationRun).filter(EvaluationRun.eval_run_id == eval_run_id).first()
            if run is None:
                return
            run.status = "running"
            run.started_at = run.started_at or _utcnow()
            run.completed_at = None
            run.last_error = None
            db.commit()

        items = []
        with db_session() as db:
            retry_cutoff = _utcnow() - timedelta(seconds=60)
            items = [
                item.eval_item_id
                for item in db.query(EvaluationRunItem)
                .filter(EvaluationRunItem.eval_run_id == eval_run_id)
                .filter(EvaluationRunItem.status.in_(["pending", "failed"]))
                # failed items must wait at least 60s before retry
                .filter(or_(
                    EvaluationRunItem.status == "pending",
                    EvaluationRunItem.updated_at < retry_cutoff,
                ))
                .order_by(EvaluationRunItem.created_at.asc())
                .all()
            ]

        for eval_item_id in items:
            await EvaluationRunService.evaluate_trace_item(eval_item_id, evaluator=evaluator)

        with db_session() as db:
            EvaluationRunService._refresh_run_status(db, eval_run_id)
            db.commit()

    @staticmethod
    async def process_next_pending(limit: int = 5, evaluator: EvaluatorFn | None = None) -> int:
        with db_session() as db:
            run_ids = [
                run.eval_run_id
                for run in db.query(EvaluationRun)
                .filter(
                    EvaluationRun.status.in_(["pending", "failed", "partial"]),
                    EvaluationRun.scope != "experiment",
                )
                .order_by(EvaluationRun.created_at.asc())
                .limit(limit)
                .all()
            ]
        for eval_run_id in run_ids:
            await EvaluationRunService.process_run(eval_run_id, evaluator=evaluator)
        return len(run_ids)

    @staticmethod
    async def evaluate_trace_item(
        eval_item_id: str,
        evaluator: EvaluatorFn | None = None,
        *,
        return_result: bool = False,
    ):
        evaluator = evaluator or EvaluationRunService._default_evaluator
        result_obj = None
        with db_session() as db:
            item = db.query(EvaluationRunItem).filter(EvaluationRunItem.eval_item_id == eval_item_id).first()
            if item is None:
                return
            if item.status == "completed":
                return
            run = db.query(EvaluationRun).filter(EvaluationRun.eval_run_id == item.eval_run_id).first()
            trace = db.query(Trace).filter(Trace.observation_id == item.trace_id).first()
            dataset_item = None
            if item.dataset_item_id:
                dataset_item = db.query(DatasetItem).filter(DatasetItem.dataset_item_id == item.dataset_item_id).first()
            if run is None or (trace is None and dataset_item is None):
                now = _utcnow()
                item.status = "skipped"
                item.error = "source item not found" if run is not None else "evaluation run not found"
                item.completed_at = now
                item.updated_at = now
                db.commit()
                return
            now = _utcnow()
            item.status = "running"
            item.started_at = item.started_at or now
            item.completed_at = None
            item.error = None
            item.updated_at = now
            eval_run_id = item.eval_run_id
            trace_id = item.trace_id
            dataset_id = run.dataset_id
            dataset_item_id = item.dataset_item_id
            db.commit()

            if dataset_item is not None:
                user_task, answer, sources, trace_summary, extra_context = _dataset_item_payload(dataset_item)
                context = _json_obj(dataset_item.context) or {}
                task_type = context.get("task_type") or "dataset_eval"
                route_intent = context.get("route_intent")
            else:
                user_task, answer, sources, trace_summary, extra_context = _trace_payload(trace)
                task_type = trace.task_type or "chat_turn"
                route_intent = trace.route_intent

        try:
            import contextlib
            from langfuse import propagate_attributes
            _ctx = propagate_attributes(
                session_id=f"eval:{eval_run_id}",
                metadata={"source_trace_id": trace_id, "eval_item_id": eval_item_id},
            ) if trace_id else contextlib.nullcontext()
            with _ctx:
                result = await evaluator(
                    user_task=user_task,
                    answer=answer,
                    task_type=task_type,
                    route_intent=route_intent,
                    sources=sources,
                    trace_summary=trace_summary,
                    extra_context=extra_context,
                )
            result_obj = result
            result_dict = _result_to_dict(result)
            score_payloads = evaluation_result_to_score_payloads(
                eval_run_id=eval_run_id,
                eval_item_id=eval_item_id,
                trace_id=trace_id,
                result=result_dict,
                route_intent=route_intent,
                task_type=task_type,
                dataset_id=dataset_id,
                dataset_item_id=dataset_item_id,
            )
            score_ids = [payload["score_id"] for payload in score_payloads]

            with db_session() as db:
                item = db.query(EvaluationRunItem).filter(EvaluationRunItem.eval_item_id == eval_item_id).first()
                trace = db.query(Trace).filter(Trace.observation_id == item.trace_id).first() if item else None
                if item is None or trace is None:
                    return
                # Ensure the trace exists in traces_v2 before writing scores
                # (FK constraint: scores.trace_id → traces_v2.trace_id)
                if trace is not None and TraceRepository.get_trace(db, trace.observation_id) is None:
                    TraceRepository.upsert_trace(db, {
                        "trace_id": trace.observation_id,
                        "name": trace.agent_name or trace.name or "trace",
                        "thread_id": trace.thread_id,
                        "user_id": trace.user_id,
                        "environment": trace.environment or "default",
                        "start_time": trace.start_time.isoformat() if trace.start_time else None,
                        "end_time": trace.end_time.isoformat() if trace.end_time else None,
                    })
                for payload in score_payloads:
                    ScoreRepository.upsert_score(db, payload, sync_legacy_cache=False)
                if item.dataset_item_id is None and trace is not None:
                    ScoreRepository.sync_legacy_trace_cache(db, trace.observation_id)
                # Write evaluation result as a local observation so it shows in trace detail
                if trace_id:
                    try:
                        from services.trace_ingestion import TraceEventIngestor
                        now_iso = _utcnow().isoformat()
                        TraceEventIngestor.enqueue_sync([{
                            "event_type": "observation-create",
                            "body": {
                                "observation_id": f"eval-obs:{eval_item_id}",
                                "trace_id": trace_id,
                                "type": "EVALUATOR",
                                "name": "Evaluate Output",
                                "start_time": now_iso,
                                "end_time": now_iso,
                                "output": {k: v for k, v in result_dict.items()
                                           if k in ("overall", "grounding", "task_fit",
                                                    "completeness", "verdict", "issues")},
                                "metadata": {
                                    "eval_run_id": eval_run_id,
                                    "eval_item_id": eval_item_id,
                                    "task_type": task_type,
                                },
                                "level": "DEFAULT",
                            },
                        }])
                    except Exception:
                        pass
                now = _utcnow()
                item.status = "completed"
                item.score_ids = _json_text(score_ids)
                item.error = None
                item.completed_at = now
                item.updated_at = now
                EvaluationRunService._refresh_run_status(db, item.eval_run_id, finalize=False)
                db.commit()
            return result_obj if return_result else None
        except Exception as exc:
            with db_session() as db:
                item = db.query(EvaluationRunItem).filter(EvaluationRunItem.eval_item_id == eval_item_id).first()
                if item:
                    now = _utcnow()
                    item.status = "failed"
                    item.error = str(exc)
                    item.completed_at = now
                    item.updated_at = now
                    EvaluationRunService._refresh_run_status(db, item.eval_run_id, finalize=False)
                    db.commit()
            if return_result:
                raise

    @staticmethod
    def retry_failed_items(db: Session, eval_run_id: str) -> int:
        rows = db.query(EvaluationRunItem).filter(
            EvaluationRunItem.eval_run_id == eval_run_id,
            EvaluationRunItem.status == "failed",
        ).all()
        now = _utcnow()
        for item in rows:
            item.status = "pending"
            item.error = None
            item.started_at = None
            item.completed_at = None
            item.updated_at = now
        run = db.query(EvaluationRun).filter(EvaluationRun.eval_run_id == eval_run_id).first()
        if run and rows:
            run.status = "pending"
            run.completed_at = None
            run.last_error = None
            run.updated_at = now
            EvaluationRunService._refresh_run_status(db, eval_run_id, finalize=False)
        db.commit()
        return len(rows)

    @staticmethod
    def recover_stale_items(db: Session, *, timeout_seconds: int = 900) -> int:
        cutoff = _utcnow() - timedelta(seconds=timeout_seconds)
        rows = (
            db.query(EvaluationRunItem)
            .filter(
                EvaluationRunItem.status == "running",
                EvaluationRunItem.updated_at.isnot(None),
                EvaluationRunItem.updated_at < cutoff,
            )
            .all()
        )
        now = _utcnow()
        for item in rows:
            item.status = "failed"
            item.error = "stale running item recovered"
            item.completed_at = now
            item.updated_at = now
        return len(rows)

    @staticmethod
    def recover_stale_runs(db: Session, *, timeout_seconds: int = 900) -> int:
        cutoff = _utcnow() - timedelta(seconds=timeout_seconds)
        rows = (
            db.query(EvaluationRun)
            .filter(
                EvaluationRun.status == "running",
                EvaluationRun.updated_at.isnot(None),
                EvaluationRun.updated_at < cutoff,
            )
            .all()
        )
        recovered = 0
        now = _utcnow()
        for run in rows:
            items = db.query(EvaluationRunItem).filter(EvaluationRunItem.eval_run_id == run.eval_run_id).all()
            unfinished = [item for item in items if item.status in {"pending", "running", "failed"}]
            if unfinished:
                run.status = "pending"
                run.completed_at = None
                run.last_error = "stale running run recovered"
                run.updated_at = now
                recovered += 1
            else:
                EvaluationRunService._refresh_run_status(db, run.eval_run_id)
        return recovered

    @staticmethod
    def list_runs(
        db: Session,
        *,
        limit: int = 50,
        offset: int = 0,
        status: str | None = None,
        name: str | None = None,
        scope: str | None = None,
    ) -> dict:
        q = db.query(EvaluationRun)
        if status and status != "all":
            q = q.filter(EvaluationRun.status == status)
        if name and name != "all":
            q = q.filter(EvaluationRun.name == name)
        if scope and scope != "all":
            q = q.filter(EvaluationRun.scope == scope)
        total = q.count()
        rows = q.order_by(EvaluationRun.created_at.desc()).offset(offset).limit(limit).all()
        return {"runs": [_run_payload(row) for row in rows], "total": total}

    @staticmethod
    def get_run_detail(db: Session, eval_run_id: str) -> dict:
        row = db.query(EvaluationRun).filter(EvaluationRun.eval_run_id == eval_run_id).first()
        if row is None:
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="Evaluation run not found")
        return _run_payload(row, include_items=True)

    @staticmethod
    async def _default_evaluator(**kwargs):
        from agents.evaluation_agent import evaluate_output
        return await evaluate_output(**kwargs)

    @staticmethod
    def _refresh_run_status(db: Session, eval_run_id: str, *, finalize: bool = True) -> None:
        run = db.query(EvaluationRun).filter(EvaluationRun.eval_run_id == eval_run_id).first()
        if run is None:
            return
        items = db.query(EvaluationRunItem).filter(EvaluationRunItem.eval_run_id == eval_run_id).all()
        completed = [item for item in items if item.status == "completed"]
        failed = [item for item in items if item.status == "failed"]
        skipped = [item for item in items if item.status == "skipped"]
        active = [item for item in items if item.status in {"pending", "running"}]
        run.succeeded_count = len(completed)
        run.failed_count = len(failed) + len(skipped)
        run.total_count = len(items)
        errors = [item.error for item in failed + skipped if item.error]
        run.last_error = errors[-1] if errors else None
        if active:
            run.status = "running"
            if finalize:
                run.completed_at = None
        elif finalize:
            run.completed_at = _utcnow()
            if run.total_count == 0:
                run.status = "completed"
            elif run.succeeded_count == run.total_count:
                run.status = "completed"
            elif run.succeeded_count > 0:
                run.status = "partial"
            else:
                run.status = "failed"
        run.updated_at = _utcnow()
