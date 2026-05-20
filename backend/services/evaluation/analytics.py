"""Read-only analytics for dataset and evaluation-run scores."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session

from db import Dataset, DatasetItem, EvaluationRun, ExperimentRun, ExperimentRunItem, Score


DIMENSION_NAMES = [
    "grounding",
    "task_fit",
    "completeness",
    "specificity",
    "source_quality",
    "uncertainty_honesty",
    "format_fit",
]


def _json_obj(value: Any) -> Any:
    if value is None or isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return value


def _metadata(score: Score) -> dict:
    value = _json_obj(score.metadata_json)
    return value if isinstance(value, dict) else {}


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() + "Z" if value else None


def _score_matches_eval_run(score: Score, eval_run_id: str) -> bool:
    meta = _metadata(score)
    return score.eval_run_id == eval_run_id or meta.get("eval_run_id") == eval_run_id or score.execution_trace_id == eval_run_id


def _score_matches_dataset(score: Score, dataset_id: str, eval_run_ids: set[str]) -> bool:
    meta = _metadata(score)
    if score.dataset_id == dataset_id or meta.get("dataset_id") == dataset_id:
        return True
    return bool(score.execution_trace_id and score.execution_trace_id in eval_run_ids)


def _score_matches_dataset_item(score: Score, dataset_item_id: str) -> bool:
    return score.dataset_item_id == dataset_item_id or _metadata(score).get("dataset_item_id") == dataset_item_id


def _score_matches_experiment(score: Score, experiment_run_id: str, eval_run_ids: set[str]) -> bool:
    meta = _metadata(score)
    if score.experiment_run_id == experiment_run_id or meta.get("experiment_run_id") == experiment_run_id:
        return True
    return bool(score.execution_trace_id and score.execution_trace_id in eval_run_ids)


def _candidate_scores_for_eval_run(db: Session, eval_run_id: str) -> list[Score]:
    explicit_rows = (
        db.query(Score)
        .filter(or_(Score.eval_run_id == eval_run_id, Score.execution_trace_id == eval_run_id))
        .all()
    )
    if explicit_rows:
        return explicit_rows

    rows = db.query(Score).filter(Score.metadata_json.isnot(None)).all()
    return [score for score in rows if _score_matches_eval_run(score, eval_run_id)]


def _candidate_scores_for_dataset(db: Session, dataset_id: str, eval_run_ids: set[str]) -> list[Score]:
    conditions = [Score.dataset_id == dataset_id]
    if eval_run_ids:
        conditions.append(Score.execution_trace_id.in_(eval_run_ids))
    explicit_rows = db.query(Score).filter(or_(*conditions)).all()
    if explicit_rows:
        return [score for score in explicit_rows if _score_matches_dataset(score, dataset_id, eval_run_ids)]

    rows = db.query(Score).filter(Score.metadata_json.isnot(None)).all()
    return [score for score in rows if _score_matches_dataset(score, dataset_id, eval_run_ids)]


def _candidate_scores_for_dataset_item(db: Session, dataset_item_id: str) -> list[Score]:
    explicit_rows = db.query(Score).filter(Score.dataset_item_id == dataset_item_id).order_by(Score.created_at.asc()).all()
    if explicit_rows:
        return explicit_rows

    rows = db.query(Score).filter(Score.metadata_json.isnot(None)).order_by(Score.created_at.asc()).all()
    return [score for score in rows if _score_matches_dataset_item(score, dataset_item_id)]


def _candidate_scores_for_experiment(db: Session, experiment_run_id: str, eval_run_ids: set[str]) -> list[Score]:
    conditions = [Score.experiment_run_id == experiment_run_id]
    if eval_run_ids:
        conditions.append(Score.execution_trace_id.in_(eval_run_ids))
    explicit_rows = db.query(Score).filter(or_(*conditions)).all()
    if explicit_rows:
        return [score for score in explicit_rows if _score_matches_experiment(score, experiment_run_id, eval_run_ids)]

    rows = db.query(Score).filter(Score.metadata_json.isnot(None)).all()
    return [score for score in rows if _score_matches_experiment(score, experiment_run_id, eval_run_ids)]


def _empty_stats(total: int = 0) -> dict:
    return {
        "total": total,
        "scored": 0,
        "unscored": total,
        "avg_score": None,
        "low_quality_count": 0,
        "low_quality_pct": None,
        "distribution": [{"bucket": key, "count": 0} for key in ["0-1", "1-2", "2-3", "3-4", "4-5"]],
        "dimension_avgs": {name: None for name in DIMENSION_NAMES},
    }


def _subject_key(score: Score) -> str | None:
    meta = _metadata(score)
    return (
        score.dataset_item_id
        or meta.get("dataset_item_id")
        or score.eval_item_id
        or meta.get("eval_item_id")
        or score.trace_id
        or score.observation_id
    )


def _stats_from_scores(scores: list[Score], *, total: int) -> dict:
    if not scores:
        return _empty_stats(total)

    overall_scores = [score for score in scores if score.name == "overall" and score.value is not None]
    if not overall_scores:
        return _empty_stats(total)

    scored_subjects = {_subject_key(score) for score in overall_scores if _subject_key(score)}
    scored = len(scored_subjects) if scored_subjects else len(overall_scores)
    values = [float(score.value or 0) for score in overall_scores]

    buckets = {"0-1": 0, "1-2": 0, "2-3": 0, "3-4": 0, "4-5": 0}
    for value in values:
        if value < 1:
            buckets["0-1"] += 1
        elif value < 2:
            buckets["1-2"] += 1
        elif value < 3:
            buckets["2-3"] += 1
        elif value < 4:
            buckets["3-4"] += 1
        else:
            buckets["4-5"] += 1

    dimension_avgs = {}
    for name in DIMENSION_NAMES:
        vals = [float(score.value) for score in scores if score.name == name and score.value is not None]
        dimension_avgs[name] = round(sum(vals) / len(vals), 2) if vals else None

    low = sum(1 for value in values if value < 3)
    return {
        "total": total,
        "scored": scored,
        "unscored": max(total - scored, 0),
        "avg_score": round(sum(values) / len(values), 2) if values else None,
        "low_quality_count": low,
        "low_quality_pct": round(low / len(values) * 100, 1) if values else None,
        "distribution": [{"bucket": key, "count": value} for key, value in buckets.items()],
        "dimension_avgs": dimension_avgs,
    }


def _run_summary(row: EvaluationRun) -> dict:
    return {
        "eval_run_id": row.eval_run_id,
        "name": row.name,
        "status": row.status,
        "scope": row.scope,
        "dataset_id": row.dataset_id,
        "total_count": row.total_count,
        "succeeded_count": row.succeeded_count,
        "failed_count": row.failed_count,
        "created_at": _iso(row.created_at),
        "completed_at": _iso(row.completed_at),
    }


class EvaluationAnalyticsService:
    @staticmethod
    def eval_run_score_stats(db: Session, eval_run_id: str) -> dict:
        run = db.query(EvaluationRun).filter(EvaluationRun.eval_run_id == eval_run_id).first()
        if run is None:
            raise HTTPException(status_code=404, detail="Evaluation run not found")
        scores = _candidate_scores_for_eval_run(db, eval_run_id)
        stats = _stats_from_scores(scores, total=run.total_count or 0)
        stats["eval_run_id"] = eval_run_id
        return stats

    @staticmethod
    def dataset_score_stats(db: Session, dataset_id: str) -> dict:
        dataset = db.query(Dataset).filter(Dataset.dataset_id == dataset_id).first()
        if dataset is None or dataset.is_archived:
            raise HTTPException(status_code=404, detail="Dataset not found")

        total = (
            db.query(DatasetItem)
            .filter(DatasetItem.dataset_id == dataset_id, DatasetItem.is_archived.is_(False), DatasetItem.is_deleted.is_(False))
            .count()
        )
        runs = db.query(EvaluationRun).filter(EvaluationRun.dataset_id == dataset_id).all()
        eval_run_ids = {run.eval_run_id for run in runs}
        scores = _candidate_scores_for_dataset(db, dataset_id, eval_run_ids)
        stats = _stats_from_scores(scores, total=total)
        stats["dataset_id"] = dataset_id
        stats["eval_run_count"] = len(runs)
        return stats

    @staticmethod
    def dataset_eval_runs(db: Session, dataset_id: str, *, limit: int = 50, offset: int = 0) -> dict:
        dataset = db.query(Dataset).filter(Dataset.dataset_id == dataset_id).first()
        if dataset is None or dataset.is_archived:
            raise HTTPException(status_code=404, detail="Dataset not found")
        q = db.query(EvaluationRun).filter(EvaluationRun.dataset_id == dataset_id)
        total = q.count()
        rows = q.order_by(EvaluationRun.created_at.desc()).offset(offset).limit(limit).all()
        return {"dataset_id": dataset_id, "runs": [_run_summary(row) for row in rows], "total": total}

    @staticmethod
    def dataset_item_score_history(db: Session, dataset_id: str, dataset_item_id: str) -> dict:
        dataset = db.query(Dataset).filter(Dataset.dataset_id == dataset_id).first()
        if dataset is None or dataset.is_archived:
            raise HTTPException(status_code=404, detail="Dataset not found")
        item = (
            db.query(DatasetItem)
            .filter(
                DatasetItem.dataset_id == dataset_id,
                DatasetItem.dataset_item_id == dataset_item_id,
                DatasetItem.is_archived.is_(False),
                DatasetItem.is_deleted.is_(False),
            )
            .first()
        )
        if item is None:
            raise HTTPException(status_code=404, detail="Dataset item not found")

        scores = _candidate_scores_for_dataset_item(db, dataset_item_id)
        grouped: dict[str, dict] = {}
        for score in scores:
            meta = _metadata(score)
            eval_run_id = score.eval_run_id or meta.get("eval_run_id") or score.execution_trace_id or "unknown"
            entry = grouped.setdefault(eval_run_id, {
                "eval_run_id": eval_run_id,
                "eval_item_id": score.eval_item_id or meta.get("eval_item_id"),
                "dataset_id": dataset_id,
                "dataset_item_id": dataset_item_id,
                "score_ids": [],
                "overall": None,
                "dimension_scores": {},
                "verdict": None,
                "comment": None,
                "created_at": _iso(score.created_at),
            })
            entry["score_ids"].append(score.score_id)
            if score.name == "overall" and score.value is not None:
                entry["overall"] = float(score.value)
            elif score.name in DIMENSION_NAMES and score.value is not None:
                entry["dimension_scores"][score.name] = float(score.value)
            elif score.name == "verdict" and score.string_value:
                entry["verdict"] = score.string_value
            if score.comment:
                entry["comment"] = score.comment
            if score.created_at and (entry["created_at"] is None or _iso(score.created_at) < entry["created_at"]):
                entry["created_at"] = _iso(score.created_at)

        history = sorted(grouped.values(), key=lambda item: item["created_at"] or "")
        return {
            "dataset_id": dataset_id,
            "dataset_item_id": dataset_item_id,
            "source_trace_id": item.source_trace_id,
            "history": history,
            "total": len(history),
        }

    @staticmethod
    def experiment_score_stats(db: Session, experiment_run_id: str) -> dict:
        run = db.query(ExperimentRun).filter(ExperimentRun.experiment_run_id == experiment_run_id).first()
        if run is None:
            raise HTTPException(status_code=404, detail="Experiment run not found")
        total = (
            db.query(ExperimentRunItem)
            .filter(ExperimentRunItem.experiment_run_id == experiment_run_id)
            .count()
        )
        eval_run_ids = {
            eval_run_id
            for (eval_run_id,) in (
                db.query(ExperimentRunItem.eval_run_id)
                .filter(
                    ExperimentRunItem.experiment_run_id == experiment_run_id,
                    ExperimentRunItem.eval_run_id.isnot(None),
                )
                .distinct()
                .all()
            )
            if eval_run_id
        }
        scores = _candidate_scores_for_experiment(db, experiment_run_id, eval_run_ids)
        stats = _stats_from_scores(scores, total=total)
        stats["experiment_run_id"] = experiment_run_id
        stats["eval_run_count"] = len(eval_run_ids)
        return stats

    @staticmethod
    def compare_eval_runs(db: Session, left: str, right: str) -> dict:
        left_stats = EvaluationAnalyticsService.eval_run_score_stats(db, left)
        right_stats = EvaluationAnalyticsService.eval_run_score_stats(db, right)

        def delta_value(a, b):
            if a is None or b is None:
                return None
            return round(float(b) - float(a), 2)

        dimension_delta = {
            name: delta_value(left_stats["dimension_avgs"].get(name), right_stats["dimension_avgs"].get(name))
            for name in DIMENSION_NAMES
        }
        return {
            "left": left_stats,
            "right": right_stats,
            "delta": {
                "avg_score": delta_value(left_stats.get("avg_score"), right_stats.get("avg_score")),
                "low_quality_count": (right_stats.get("low_quality_count") or 0) - (left_stats.get("low_quality_count") or 0),
                "dimension_avgs": dimension_delta,
            },
        }
