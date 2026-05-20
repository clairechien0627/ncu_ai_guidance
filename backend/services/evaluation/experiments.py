"""Dataset replay experiments for Trace System v2."""
from __future__ import annotations
from utils import new_id

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

from fastapi import HTTPException
from sqlalchemy.orm import Session

from db import (
    Dataset,
    DatasetItem,
    EvaluationRun,
    EvaluationRunItem,
    ExperimentRun,
    ExperimentRunItem,
    Score,
    db_session,
)
from .runs import evaluation_result_to_score_payloads
from services.trace_repositories import ScoreRepository


GeneratorFn = Callable[..., Awaitable[object]]
EvaluatorFn = Callable[..., Awaitable[object]]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _json_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def _json_obj(value: Any) -> Any:
    if value is None or isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return value


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() + "Z" if value else None


def _user_task_from_item(item: DatasetItem) -> str:
    input_obj = _json_obj(item.input) or {}
    if isinstance(input_obj, dict):
        messages = input_obj.get("messages") if isinstance(input_obj.get("messages"), list) else []
        for msg in messages:
            if isinstance(msg, dict) and msg.get("role") in ("human", "user"):
                return str(msg.get("content") or "")
    return json.dumps(input_obj, ensure_ascii=False)[:1200] if input_obj else ""


def _answer_from_output(value: Any) -> str:
    output = _json_obj(value)
    if isinstance(output, dict):
        return str(output.get("answer") or output.get("response") or output.get("content") or output)
    return str(output or "")


def _normalize_generator_result(result: object) -> tuple[Any, dict, str | None]:
    if hasattr(result, "model_dump"):
        result = result.model_dump()
    if not isinstance(result, dict):
        return {"answer": str(result or "")}, {}, None

    output = result.get("output")
    if output is None:
        output = result.get("answer") or result.get("response") or result.get("content")
    if not isinstance(output, (dict, list)):
        output = {"answer": str(output or "")}

    context = result.get("context") if isinstance(result.get("context"), dict) else {}
    for key in ["sources", "task_type", "route_intent", "agent_name", "prompt_name", "prompt_version"]:
        if key in result and key not in context:
            context[key] = result[key]
    trace_id = result.get("trace_id") or result.get("agent_observation_id")
    return output, context, trace_id


class GeneratorAdapter:
    name = "base"
    supports_model_override = False
    supports_prompt_override = False

    def validate_requested_config(self, *, model: str | None, prompt_name: str | None, prompt_version: str | None) -> None:
        unsupported: list[str] = []
        if model and not self.supports_model_override:
            unsupported.append("model")
        if (prompt_name or prompt_version) and not self.supports_prompt_override:
            unsupported.append("prompt_name/prompt_version")
        if unsupported:
            raise HTTPException(
                status_code=400,
                detail=f"Generator adapter {self.name!r} does not support override(s): {', '.join(unsupported)}",
            )

    def requested_config(
        self,
        *,
        model: str | None,
        prompt_name: str | None,
        prompt_version: str | None,
        runtime_config: dict | None,
    ) -> dict:
        return {
            "target_agent": self.name,
            "model": model,
            "prompt_name": prompt_name,
            "prompt_version": prompt_version,
            "runtime_config": runtime_config or {},
        }

    def applied_config(self, *, runtime_config: dict | None) -> dict:
        return {
            "target_agent": self.name,
            "runtime_config": {"use_mini": bool((runtime_config or {}).get("use_mini"))},
        }

    async def generate(self, **_kwargs):
        raise NotImplementedError


class RouterAgentCurrentAdapter(GeneratorAdapter):
    name = "router_agent_current"

    async def generate(self, **kwargs):
        from agents.router_agent import route_agent_message

        input_obj = kwargs.get("input") if isinstance(kwargs.get("input"), dict) else {}
        context = kwargs.get("context") if isinstance(kwargs.get("context"), dict) else {}
        message = _user_task_from_raw_input(input_obj)
        if not message:
            raise ValueError("Dataset item input has no user message to replay")
        document_ids = _document_ids_from_context(input_obj, context)
        result = await route_agent_message(
            message,
            kwargs.get("experiment_item_id") or kwargs.get("experiment_run_id") or new_id(),
            document_ids,
            use_mini=bool((kwargs.get("runtime_config") or {}).get("use_mini")),
        )
        requested_config = {
            "target_agent": kwargs.get("target_agent"),
            "model": kwargs.get("model"),
            "prompt_name": kwargs.get("prompt_name"),
            "prompt_version": kwargs.get("prompt_version"),
            "runtime_config": kwargs.get("runtime_config") or {},
        }
        applied_config = {
            "target_agent": result.agent_name,
            "prompt_name": result.prompt_name,
            "prompt_version": result.prompt_version,
            "runtime_config": {"use_mini": bool((kwargs.get("runtime_config") or {}).get("use_mini"))},
        }
        return {
            "output": {"answer": result.response},
            "context": {
                "sources": result.sources,
                "task_type": result.task_type,
                "route_intent": result.route_intent,
                "agent_name": result.agent_name,
                "prompt_name": result.prompt_name,
                "prompt_version": result.prompt_version,
                "requested_config": requested_config,
                "applied_config": applied_config,
            },
            "trace_id": result.observation_id,
        }


GENERATOR_ADAPTERS: dict[str, GeneratorAdapter] = {
    "router_agent_current": RouterAgentCurrentAdapter(),
    "router_agent": RouterAgentCurrentAdapter(),
}


def _generator_adapter(target_agent: str | None) -> GeneratorAdapter:
    key = target_agent or "router_agent_current"
    adapter = GENERATOR_ADAPTERS.get(key)
    if adapter is None:
        raise HTTPException(status_code=400, detail=f"Unsupported generator adapter {key!r}")
    return adapter


def _experiment_payload(row: ExperimentRun, *, include_items: bool = False) -> dict:
    metadata = _json_obj(row.metadata_json) or {}
    payload = {
        "experiment_run_id": row.experiment_run_id,
        "dataset_id": row.dataset_id,
        "name": row.name,
        "status": row.status,
        "target_agent": row.target_agent,
        "model": row.model,
        "prompt_name": row.prompt_name,
        "prompt_version": row.prompt_version,
        "runtime_config": _json_obj(row.runtime_config) or {},
        "metadata": metadata,
        "requested_config": metadata.get("requested_config") or {},
        "applied_config": metadata.get("applied_config") or {},
        "config_warning": metadata.get("config_warning"),
        "total_count": row.total_count,
        "succeeded_count": row.succeeded_count,
        "failed_count": row.failed_count,
        "last_error": row.last_error,
        "started_at": _iso(row.started_at),
        "completed_at": _iso(row.completed_at),
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }
    if include_items:
        payload["items"] = [
            _experiment_item_payload(item)
            for item in sorted(row.items, key=lambda item: item.created_at or datetime.min)
        ]
    return payload


def _experiment_item_payload(row: ExperimentRunItem) -> dict:
    return {
        "experiment_item_id": row.experiment_item_id,
        "experiment_run_id": row.experiment_run_id,
        "dataset_item_id": row.dataset_item_id,
        "status": row.status,
        "generated_output": _json_obj(row.generated_output),
        "generated_context": _json_obj(row.generated_context),
        "trace_id": row.trace_id,
        "eval_run_id": row.eval_run_id,
        "error": row.error,
        "started_at": _iso(row.started_at),
        "completed_at": _iso(row.completed_at),
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }


class ExperimentRunService:
    @staticmethod
    def create_dataset_replay(
        db: Session,
        *,
        dataset_id: str,
        name: str = "dataset-replay",
        target_agent: str | None = None,
        model: str | None = None,
        prompt_name: str | None = None,
        prompt_version: str | None = None,
        runtime_config: dict | None = None,
        metadata: dict | None = None,
    ) -> ExperimentRun | None:
        dataset = db.query(Dataset).filter(Dataset.dataset_id == dataset_id, Dataset.is_archived.is_(False)).first()
        if dataset is None:
            raise HTTPException(status_code=404, detail="Dataset not found")
        items = (
            db.query(DatasetItem)
            .filter(DatasetItem.dataset_id == dataset_id, DatasetItem.is_archived.is_(False))
            .order_by(DatasetItem.created_at.asc())
            .all()
        )
        if not items:
            return None

        experiment_run_id = f"exp-{new_id()}"
        now = _utcnow()
        adapter = _generator_adapter(target_agent)
        adapter.validate_requested_config(model=model, prompt_name=prompt_name, prompt_version=prompt_version)
        requested_config = adapter.requested_config(
            model=model,
            prompt_name=prompt_name,
            prompt_version=prompt_version,
            runtime_config=runtime_config,
        )
        applied_config = adapter.applied_config(runtime_config=runtime_config)
        run_metadata = {
            "source": "dataset_replay",
            **(metadata or {}),
            "requested_config": requested_config,
            "applied_config": applied_config,
        }
        run = ExperimentRun(
            experiment_run_id=experiment_run_id,
            dataset_id=dataset_id,
            name=name,
            status="pending",
            target_agent=adapter.name,
            model=model,
            prompt_name=prompt_name,
            prompt_version=prompt_version,
            runtime_config=_json_text(runtime_config or {}),
            metadata_json=_json_text(run_metadata),
            total_count=len(items),
            created_at=now,
            updated_at=now,
        )
        db.add(run)
        for item in items:
            db.add(ExperimentRunItem(
                experiment_item_id=f"{experiment_run_id}:{item.dataset_item_id}",
                experiment_run_id=experiment_run_id,
                dataset_item_id=item.dataset_item_id,
                status="pending",
                created_at=now,
                updated_at=now,
            ))
        db.commit()
        db.refresh(run)
        return run

    @staticmethod
    async def process_run(experiment_run_id: str, generator: GeneratorFn | None = None) -> None:
        generator = generator or ExperimentRunService._default_generator
        with db_session() as db:
            run = db.query(ExperimentRun).filter(ExperimentRun.experiment_run_id == experiment_run_id).first()
            if run is None:
                return
            run.status = "running"
            run.started_at = run.started_at or _utcnow()
            run.completed_at = None
            run.last_error = None
            db.commit()

        with db_session() as db:
            item_ids = [
                item.experiment_item_id
                for item in db.query(ExperimentRunItem)
                .filter(ExperimentRunItem.experiment_run_id == experiment_run_id)
                .filter(ExperimentRunItem.status.in_(["pending", "failed"]))
                .order_by(ExperimentRunItem.created_at.asc())
                .all()
            ]

        for experiment_item_id in item_ids:
            await ExperimentRunService.generate_item(experiment_item_id, generator=generator)

        with db_session() as db:
            ExperimentRunService._refresh_run_status(db, experiment_run_id)
            db.commit()

    @staticmethod
    async def process_next_pending(limit: int = 5, generator: GeneratorFn | None = None) -> int:
        with db_session() as db:
            run_ids = [
                run.experiment_run_id
                for run in db.query(ExperimentRun)
                .filter(ExperimentRun.status.in_(["pending", "failed", "partial"]))
                .order_by(ExperimentRun.created_at.asc())
                .limit(limit)
                .all()
            ]
        for experiment_run_id in run_ids:
            await ExperimentRunService.process_run(experiment_run_id, generator=generator)
        return len(run_ids)

    @staticmethod
    async def generate_item(experiment_item_id: str, generator: GeneratorFn | None = None) -> None:
        generator = generator or ExperimentRunService._default_generator
        with db_session() as db:
            item = db.query(ExperimentRunItem).filter(ExperimentRunItem.experiment_item_id == experiment_item_id).first()
            if item is None or item.status == "completed":
                return
            run = db.query(ExperimentRun).filter(ExperimentRun.experiment_run_id == item.experiment_run_id).first()
            dataset_item = db.query(DatasetItem).filter(DatasetItem.dataset_item_id == item.dataset_item_id).first()
            if run is None or dataset_item is None:
                now = _utcnow()
                item.status = "skipped"
                item.error = "source item not found" if run is not None else "experiment run not found"
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
            db.commit()

            payload = {
                "experiment_run_id": run.experiment_run_id,
                "experiment_item_id": item.experiment_item_id,
                "dataset_id": run.dataset_id,
                "dataset_item_id": dataset_item.dataset_item_id,
                "input": _json_obj(dataset_item.input),
                "expected_output": _json_obj(dataset_item.expected_output),
                "context": _json_obj(dataset_item.context) or {},
                "target_agent": run.target_agent,
                "model": run.model,
                "prompt_name": run.prompt_name,
                "prompt_version": run.prompt_version,
                "runtime_config": _json_obj(run.runtime_config) or {},
                "metadata": _json_obj(run.metadata_json) or {},
            }

        try:
            result = await generator(**payload)
            output, context, trace_id = _normalize_generator_result(result)
            with db_session() as db:
                item = db.query(ExperimentRunItem).filter(ExperimentRunItem.experiment_item_id == experiment_item_id).first()
                if item is None:
                    return
                now = _utcnow()
                item.status = "completed"
                item.generated_output = _json_text(output)
                item.generated_context = _json_text(context)
                item.trace_id = trace_id
                item.error = None
                item.completed_at = now
                item.updated_at = now
                ExperimentRunService._refresh_run_status(db, item.experiment_run_id, finalize=False)
                db.commit()
        except Exception as exc:
            with db_session() as db:
                item = db.query(ExperimentRunItem).filter(ExperimentRunItem.experiment_item_id == experiment_item_id).first()
                if item:
                    now = _utcnow()
                    item.status = "failed"
                    item.error = str(exc)
                    item.completed_at = now
                    item.updated_at = now
                    ExperimentRunService._refresh_run_status(db, item.experiment_run_id, finalize=False)
                    db.commit()

    @staticmethod
    def create_eval_run_for_experiment(
        db: Session,
        experiment_run_id: str,
        *,
        name: str = "experiment-evaluation",
        metadata: dict | None = None,
    ) -> EvaluationRun | None:
        run = db.query(ExperimentRun).filter(ExperimentRun.experiment_run_id == experiment_run_id).first()
        if run is None:
            raise HTTPException(status_code=404, detail="Experiment run not found")

        existing_eval_id = next((item.eval_run_id for item in run.items if item.eval_run_id), None)
        if existing_eval_id:
            existing = db.query(EvaluationRun).filter(EvaluationRun.eval_run_id == existing_eval_id).first()
            if existing:
                return existing

        items = [item for item in run.items if item.status == "completed"]
        if not items:
            return None

        eval_run_id = f"eval-{new_id()}"
        now = _utcnow()
        eval_run = EvaluationRun(
            eval_run_id=eval_run_id,
            name=name,
            status="pending",
            scope="experiment",
            dataset_id=run.dataset_id,
            dataset_item_count=len(items),
            target_trace_ids=_json_text([item.trace_id for item in items if item.trace_id]),
            prompt_name="evaluation_agent",
            metadata_json=_json_text({
                "source": "experiment_eval",
                "experiment_run_id": experiment_run_id,
                **(metadata or {}),
            }),
            total_count=len(items),
            created_at=now,
            updated_at=now,
        )
        db.add(eval_run)
        for item in items:
            item.eval_run_id = eval_run_id
            item.updated_at = now
            db.add(EvaluationRunItem(
                eval_item_id=f"{eval_run_id}:{item.experiment_item_id}",
                eval_run_id=eval_run_id,
                trace_id=item.trace_id,
                dataset_item_id=item.dataset_item_id,
                experiment_item_id=item.experiment_item_id,
                status="pending",
                created_at=now,
                updated_at=now,
            ))
        db.commit()
        db.refresh(eval_run)
        return eval_run

    @staticmethod
    async def process_experiment_eval(eval_run_id: str, evaluator: EvaluatorFn | None = None) -> None:
        evaluator = evaluator or ExperimentRunService._default_evaluator
        with db_session() as db:
            eval_run = db.query(EvaluationRun).filter(EvaluationRun.eval_run_id == eval_run_id).first()
            if eval_run is None:
                return
            meta = _json_obj(eval_run.metadata_json) or {}
            experiment_run_id = meta.get("experiment_run_id")
            if not experiment_run_id:
                return
            eval_run.status = "running"
            eval_run.started_at = eval_run.started_at or _utcnow()
            eval_run.completed_at = None
            eval_run.last_error = None
            db.commit()

            eval_item_ids = [
                item.eval_item_id
                for item in db.query(EvaluationRunItem)
                .filter(EvaluationRunItem.eval_run_id == eval_run_id)
                .filter(EvaluationRunItem.status.in_(["pending", "failed"]))
                .order_by(EvaluationRunItem.created_at.asc())
                .all()
            ]

        for eval_item_id in eval_item_ids:
            await ExperimentRunService.evaluate_experiment_item(eval_item_id, evaluator=evaluator)

        with db_session() as db:
            ExperimentRunService._refresh_eval_run_status(db, eval_run_id)
            db.commit()

    @staticmethod
    async def evaluate_experiment_item(eval_item_id: str, evaluator: EvaluatorFn | None = None) -> None:
        evaluator = evaluator or ExperimentRunService._default_evaluator
        with db_session() as db:
            eval_item = db.query(EvaluationRunItem).filter(EvaluationRunItem.eval_item_id == eval_item_id).first()
            if eval_item is None or eval_item.status == "completed":
                return
            eval_run = db.query(EvaluationRun).filter(EvaluationRun.eval_run_id == eval_item.eval_run_id).first()
            experiment_item = (
                db.query(ExperimentRunItem)
                .filter(
                    ExperimentRunItem.eval_run_id == eval_item.eval_run_id,
                    ExperimentRunItem.dataset_item_id == eval_item.dataset_item_id,
                )
                .first()
            )
            dataset_item = (
                db.query(DatasetItem)
                .filter(DatasetItem.dataset_item_id == eval_item.dataset_item_id)
                .first()
            )
            if eval_run is None or experiment_item is None or dataset_item is None:
                now = _utcnow()
                eval_item.status = "skipped"
                eval_item.error = "experiment evaluation source not found"
                eval_item.completed_at = now
                eval_item.updated_at = now
                db.commit()
                return

            now = _utcnow()
            eval_item.status = "running"
            eval_item.started_at = eval_item.started_at or now
            eval_item.completed_at = None
            eval_item.error = None
            eval_item.updated_at = now

            dataset_context = _json_obj(dataset_item.context) or {}
            generated_context = _json_obj(experiment_item.generated_context) or {}
            user_task = _user_task_from_item(dataset_item)
            answer = _answer_from_output(experiment_item.generated_output)
            sources = generated_context.get("sources") if isinstance(generated_context.get("sources"), list) else []
            trace_summary = generated_context.get("trace_summary") if isinstance(generated_context.get("trace_summary"), dict) else {}
            task_type = generated_context.get("task_type") or dataset_context.get("task_type") or "experiment_eval"
            route_intent = generated_context.get("route_intent") or dataset_context.get("route_intent")
            experiment_run_id = experiment_item.experiment_run_id
            experiment_item_id = experiment_item.experiment_item_id
            dataset_id = eval_run.dataset_id
            dataset_item_id = eval_item.dataset_item_id
            eval_run_id = eval_item.eval_run_id
            eval_item_id_value = eval_item.eval_item_id
            source_trace_id = dataset_item.source_trace_id
            expected_output = _json_obj(dataset_item.expected_output)
            trace_id = experiment_item.trace_id
            db.commit()

        try:
            result = await evaluator(
                user_task=user_task,
                answer=answer,
                task_type=task_type,
                route_intent=route_intent,
                sources=sources,
                trace_summary=trace_summary,
                extra_context={
                    "experiment_run_id": experiment_run_id,
                    "experiment_item_id": experiment_item_id,
                    "dataset_id": dataset_id,
                    "dataset_item_id": dataset_item_id,
                    "source_trace_id": source_trace_id,
                    "expected_output": expected_output,
                    "generated_context": generated_context,
                },
            )
            score_payloads = evaluation_result_to_score_payloads(
                eval_run_id=eval_run_id,
                eval_item_id=eval_item_id_value,
                trace_id=trace_id,
                result=result,
                route_intent=route_intent,
                task_type=task_type,
                dataset_id=dataset_id,
                dataset_item_id=dataset_item_id,
            )
            for payload in score_payloads:
                metadata = payload.setdefault("metadata", {})
                metadata["experiment_run_id"] = experiment_run_id
                metadata["experiment_item_id"] = experiment_item_id
                payload["experiment_run_id"] = experiment_run_id
                payload["experiment_item_id"] = experiment_item_id
            score_ids = [payload["score_id"] for payload in score_payloads]

            with db_session() as db:
                eval_item = db.query(EvaluationRunItem).filter(EvaluationRunItem.eval_item_id == eval_item_id).first()
                if eval_item is None:
                    return
                for payload in score_payloads:
                    ScoreRepository.upsert_score(db, payload, sync_legacy_cache=False)
                now = _utcnow()
                eval_item.status = "completed"
                eval_item.score_ids = _json_text(score_ids)
                eval_item.error = None
                eval_item.completed_at = now
                eval_item.updated_at = now
                ExperimentRunService._refresh_eval_run_status(db, eval_item.eval_run_id, finalize=False)
                db.commit()
        except Exception as exc:
            with db_session() as db:
                eval_item = db.query(EvaluationRunItem).filter(EvaluationRunItem.eval_item_id == eval_item_id).first()
                if eval_item:
                    now = _utcnow()
                    eval_item.status = "failed"
                    eval_item.error = str(exc)
                    eval_item.completed_at = now
                    eval_item.updated_at = now
                    ExperimentRunService._refresh_eval_run_status(db, eval_item.eval_run_id, finalize=False)
                    db.commit()

    @staticmethod
    def list_runs(
        db: Session,
        *,
        limit: int = 50,
        offset: int = 0,
        status: str | None = None,
        dataset_id: str | None = None,
        name: str | None = None,
    ) -> dict:
        q = db.query(ExperimentRun)
        if status and status != "all":
            q = q.filter(ExperimentRun.status == status)
        if dataset_id and dataset_id != "all":
            q = q.filter(ExperimentRun.dataset_id == dataset_id)
        if name and name != "all":
            q = q.filter(ExperimentRun.name == name)
        total = q.count()
        rows = q.order_by(ExperimentRun.created_at.desc()).offset(offset).limit(limit).all()
        return {"runs": [_experiment_payload(row) for row in rows], "total": total}

    @staticmethod
    def get_run_detail(db: Session, experiment_run_id: str) -> dict:
        row = db.query(ExperimentRun).filter(ExperimentRun.experiment_run_id == experiment_run_id).first()
        if row is None:
            raise HTTPException(status_code=404, detail="Experiment run not found")
        return _experiment_payload(row, include_items=True)

    @staticmethod
    def recover_stale_items(db: Session, *, timeout_seconds: int = 900) -> int:
        cutoff = _utcnow() - timedelta(seconds=timeout_seconds)
        rows = (
            db.query(ExperimentRunItem)
            .filter(
                ExperimentRunItem.status == "running",
                ExperimentRunItem.updated_at.isnot(None),
                ExperimentRunItem.updated_at < cutoff,
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
            db.query(ExperimentRun)
            .filter(
                ExperimentRun.status == "running",
                ExperimentRun.updated_at.isnot(None),
                ExperimentRun.updated_at < cutoff,
            )
            .all()
        )
        recovered = 0
        now = _utcnow()
        for run in rows:
            items = db.query(ExperimentRunItem).filter(ExperimentRunItem.experiment_run_id == run.experiment_run_id).all()
            unfinished = [item for item in items if item.status in {"pending", "running", "failed"}]
            if unfinished:
                run.status = "pending"
                run.completed_at = None
                run.last_error = "stale running run recovered"
                run.updated_at = now
                recovered += 1
            else:
                ExperimentRunService._refresh_run_status(db, run.experiment_run_id)
        return recovered

    @staticmethod
    def compare_runs(db: Session, run_a_id: str, run_b_id: str) -> dict:
        """Compare two experiment runs item-by-item.

        A is the baseline, B is the candidate. Positive deltas mean B improved.
        """
        run_a = db.query(ExperimentRun).filter(ExperimentRun.experiment_run_id == run_a_id).first()
        run_b = db.query(ExperimentRun).filter(ExperimentRun.experiment_run_id == run_b_id).first()
        if run_a is None or run_b is None:
            raise HTTPException(status_code=404, detail="One or both experiment runs not found")

        DIM_NAMES = ["grounding", "task_fit", "completeness", "specificity",
                     "source_quality", "uncertainty_honesty", "format_fit", "overall"]

        def _scores_for_run(exp_run_id: str) -> dict[str, dict[str, float]]:
            """Return {dataset_item_id: {dim: score}} for all completed items."""
            scores = (
                db.query(Score)
                .filter(
                    Score.name.in_(DIM_NAMES),
                    Score.value.isnot(None),
                )
                .all()
            )
            result: dict[str, dict[str, float]] = {}
            for s in scores:
                meta = _json_obj(s.metadata_json) or {}
                if (s.experiment_run_id or meta.get("experiment_run_id")) != exp_run_id:
                    continue
                item_id = s.dataset_item_id or meta.get("dataset_item_id") or ""
                if not item_id:
                    continue
                result.setdefault(item_id, {})[s.name] = round(float(s.value), 2)
            return result

        def _outputs_for_run(exp_run_id: str) -> dict[str, str]:
            """Return {dataset_item_id: generated_output_text}."""
            items = db.query(ExperimentRunItem).filter(
                ExperimentRunItem.experiment_run_id == exp_run_id,
                ExperimentRunItem.status == "completed",
            ).all()
            return {i.dataset_item_id: _answer_from_output(i.generated_output) for i in items}

        scores_a = _scores_for_run(run_a_id)
        scores_b = _scores_for_run(run_b_id)
        outputs_a = _outputs_for_run(run_a_id)
        outputs_b = _outputs_for_run(run_b_id)

        common_items = set(scores_a) & set(scores_b)

        REGRESSION_THRESHOLD = 0.3
        improved_count = regressed_count = neutral_count = 0
        items_out = []
        for item_id in sorted(common_items):
            a_overall = scores_a[item_id].get("overall")
            b_overall = scores_b[item_id].get("overall")
            if a_overall is None or b_overall is None:
                continue
            delta = round(b_overall - a_overall, 2)
            if delta > REGRESSION_THRESHOLD:
                status = "improved"
                improved_count += 1
            elif delta < -REGRESSION_THRESHOLD:
                status = "regressed"
                regressed_count += 1
            else:
                status = "neutral"
                neutral_count += 1
            dim_scores = {}
            for dim in DIM_NAMES:
                dim_scores[dim] = {
                    "a": scores_a[item_id].get(dim),
                    "b": scores_b[item_id].get(dim),
                    "delta": round((scores_b[item_id].get(dim) or 0) - (scores_a[item_id].get(dim) or 0), 2)
                    if scores_a[item_id].get(dim) is not None and scores_b[item_id].get(dim) is not None
                    else None,
                }
            items_out.append({
                "dataset_item_id": item_id,
                "a_score": a_overall,
                "b_score": b_overall,
                "delta": delta,
                "status": status,
                "a_output": outputs_a.get(item_id, ""),
                "b_output": outputs_b.get(item_id, ""),
                "dimension_scores": dim_scores,
            })

        # Sort by |delta| descending
        items_out.sort(key=lambda x: abs(x["delta"]), reverse=True)

        # Aggregate dimension deltas
        dimension_deltas: dict[str, dict] = {}
        for dim in DIM_NAMES:
            a_vals = [scores_a[i].get(dim) for i in common_items if scores_a[i].get(dim) is not None]
            b_vals = [scores_b[i].get(dim) for i in common_items if scores_b[i].get(dim) is not None]
            if a_vals and b_vals:
                a_avg = round(sum(a_vals) / len(a_vals), 2)
                b_avg = round(sum(b_vals) / len(b_vals), 2)
                dimension_deltas[dim] = {"a": a_avg, "b": b_avg, "delta": round(b_avg - a_avg, 2)}

        return {
            "experiment_a": _experiment_payload(run_a),
            "experiment_b": _experiment_payload(run_b),
            "baseline": _experiment_payload(run_a),
            "candidate": _experiment_payload(run_b),
            "compared_item_count": len(items_out),
            "improved_count": improved_count,
            "regressed_count": regressed_count,
            "neutral_count": neutral_count,
            "dimension_deltas": dimension_deltas,
            "items": items_out,
        }

    @staticmethod
    async def _default_generator(**kwargs):
        return await _generator_adapter(kwargs.get("target_agent")).generate(**kwargs)

    @staticmethod
    async def _default_evaluator(**kwargs):
        from agents.evaluation_agent import evaluate_output
        return await evaluate_output(**kwargs)

    @staticmethod
    def _refresh_run_status(db: Session, experiment_run_id: str, *, finalize: bool = True) -> None:
        run = db.query(ExperimentRun).filter(ExperimentRun.experiment_run_id == experiment_run_id).first()
        if run is None:
            return
        items = db.query(ExperimentRunItem).filter(ExperimentRunItem.experiment_run_id == experiment_run_id).all()
        _refresh_run_counts(run, items, finalize=finalize)

    @staticmethod
    def _refresh_eval_run_status(db: Session, eval_run_id: str, *, finalize: bool = True) -> None:
        run = db.query(EvaluationRun).filter(EvaluationRun.eval_run_id == eval_run_id).first()
        if run is None:
            return
        items = db.query(EvaluationRunItem).filter(EvaluationRunItem.eval_run_id == eval_run_id).all()
        _refresh_run_counts(run, items, finalize=finalize)


def _refresh_run_counts(run: Any, items: list, *, finalize: bool = True) -> None:
    """共用：依 items 狀態更新 run 的計數欄位與 status。
    run 需有 succeeded_count, failed_count, total_count, status,
    completed_at, last_error, updated_at 欄位（ExperimentRun / EvaluationRun 皆符合）。
    """
    completed = [i for i in items if i.status == "completed"]
    failed    = [i for i in items if i.status == "failed"]
    skipped   = [i for i in items if i.status == "skipped"]
    active    = [i for i in items if i.status in {"pending", "running"}]
    run.succeeded_count = len(completed)
    run.failed_count    = len(failed) + len(skipped)
    run.total_count     = len(items)
    errors = [i.error for i in failed + skipped if i.error]
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


def _user_task_from_raw_input(input_obj: dict) -> str:
    messages = input_obj.get("messages") if isinstance(input_obj.get("messages"), list) else []
    for msg in messages:
        if isinstance(msg, dict) and msg.get("role") in ("human", "user"):
            return str(msg.get("content") or "")
    return str(input_obj.get("message") or input_obj.get("question") or "")


def _document_ids_from_context(input_obj: dict, context: dict) -> list[int] | None:
    candidates = input_obj.get("document_ids") or context.get("document_ids")
    if not isinstance(candidates, list):
        display = context.get("display") if isinstance(context.get("display"), dict) else {}
        candidates = display.get("document_ids")
    if not isinstance(candidates, list):
        return None
    doc_ids = []
    for value in candidates:
        try:
            doc_ids.append(int(value))
        except Exception:
            continue
    return doc_ids or None
