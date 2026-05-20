"""Durable worker for evaluation and experiment runs.

The database is the reliable queue. Redis is only a wakeup signal that reduces
polling latency when it is available.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

from db import EvaluationRun, db_session
from .runs import EvaluationRunService
from .experiments import ExperimentRunService
from services.redis_service import redis_blpop, redis_enabled, redis_rpush

logger = logging.getLogger(__name__)

EVALUATION_WAKEUP_QUEUE = "evaluation_worker:wakeup"
DEFAULT_STALE_TIMEOUT_SECONDS = 900

EvaluatorFn = Callable[..., Awaitable[object]]
GeneratorFn = Callable[..., Awaitable[object]]

_worker_task: asyncio.Task | None = None
_stop_event: asyncio.Event | None = None


class EvaluationWorker:
    @staticmethod
    async def wakeup(count: int = 1) -> None:
        if redis_enabled():
            await redis_rpush(EVALUATION_WAKEUP_QUEUE, {"count": count})

    @staticmethod
    def recover_stale_records(timeout_seconds: int = DEFAULT_STALE_TIMEOUT_SECONDS) -> dict:
        with db_session() as db:
            eval_items = EvaluationRunService.recover_stale_items(db, timeout_seconds=timeout_seconds)
            eval_runs = EvaluationRunService.recover_stale_runs(db, timeout_seconds=timeout_seconds)
            exp_items = ExperimentRunService.recover_stale_items(db, timeout_seconds=timeout_seconds)
            exp_runs = ExperimentRunService.recover_stale_runs(db, timeout_seconds=timeout_seconds)
            db.commit()
        return {
            "evaluation_items": eval_items,
            "evaluation_runs": eval_runs,
            "experiment_items": exp_items,
            "experiment_runs": exp_runs,
        }

    @staticmethod
    async def process_pending_once(
        *,
        limit: int = 5,
        evaluator: EvaluatorFn | None = None,
        generator: GeneratorFn | None = None,
        recover_timeout_seconds: int = DEFAULT_STALE_TIMEOUT_SECONDS,
    ) -> dict:
        recovery = EvaluationWorker.recover_stale_records(timeout_seconds=recover_timeout_seconds)

        experiment_runs = await ExperimentRunService.process_next_pending(limit=limit, generator=generator)

        with db_session() as db:
            experiment_eval_run_ids = [
                run.eval_run_id
                for run in db.query(EvaluationRun)
                .filter(
                    EvaluationRun.status.in_(["pending", "failed", "partial"]),
                    EvaluationRun.scope == "experiment",
                )
                .order_by(EvaluationRun.created_at.asc())
                .limit(limit)
                .all()
            ]

        experiment_eval_runs = 0
        for eval_run_id in experiment_eval_run_ids:
            await ExperimentRunService.process_experiment_eval(eval_run_id, evaluator=evaluator)
            experiment_eval_runs += 1

        evaluation_runs = await EvaluationRunService.process_next_pending(limit=limit, evaluator=evaluator)
        return {
            "recovery": recovery,
            "experiment_runs": experiment_runs,
            "experiment_eval_runs": experiment_eval_runs,
            "evaluation_runs": evaluation_runs,
            "processed": experiment_runs + experiment_eval_runs + evaluation_runs,
        }


async def _worker_loop() -> None:
    assert _stop_event is not None
    while not _stop_event.is_set():
        try:
            if redis_enabled():
                await redis_blpop(EVALUATION_WAKEUP_QUEUE, timeout=15)
            else:
                await asyncio.sleep(15)
            await EvaluationWorker.process_pending_once()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("evaluation worker tick failed: %s", exc)
            await asyncio.sleep(15)


def init_evaluation_worker() -> None:
    global _worker_task, _stop_event
    if _worker_task is not None and not _worker_task.done():
        return
    _stop_event = asyncio.Event()
    _worker_task = asyncio.create_task(_worker_loop())


async def stop_evaluation_worker() -> None:
    global _worker_task, _stop_event
    if _stop_event is not None:
        _stop_event.set()
    if _worker_task is not None:
        _worker_task.cancel()
        await asyncio.gather(_worker_task, return_exceptions=True)
    _worker_task = None
    _stop_event = None
