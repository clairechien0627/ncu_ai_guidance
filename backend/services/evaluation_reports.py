"""Structured backend reports for datasets and experiments."""
from __future__ import annotations

from collections import defaultdict
import json

from fastapi import HTTPException
from sqlalchemy.orm import Session

from db import Dataset, DatasetItem, ExperimentRun, ExperimentRunItem, Score
from services.evaluation_analytics import DIMENSION_NAMES, EvaluationAnalyticsService
from services.experiments import ExperimentRunService


def _json_obj(value):
    if value is None or isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return value


def _score_dataset_id(score: Score) -> str | None:
    meta = _json_obj(score.metadata_json) or {}
    return score.dataset_id or meta.get("dataset_id")


def _score_dataset_item_id(score: Score) -> str | None:
    meta = _json_obj(score.metadata_json) or {}
    return score.dataset_item_id or meta.get("dataset_item_id")


def _score_experiment_run_id(score: Score) -> str | None:
    meta = _json_obj(score.metadata_json) or {}
    return score.experiment_run_id or meta.get("experiment_run_id")


class EvaluationReportService:
    @staticmethod
    def dataset_regression_cases(db: Session, dataset_id: str, *, limit: int = 50, threshold: float = 3.0) -> dict:
        dataset = db.query(Dataset).filter(Dataset.dataset_id == dataset_id, Dataset.is_archived.is_(False)).first()
        if dataset is None:
            raise HTTPException(status_code=404, detail="Dataset not found")

        rows = (
            db.query(Score)
            .filter(
                Score.dataset_id == dataset_id,
                Score.name == "overall",
                Score.value.isnot(None),
            )
            .all()
        )
        if not rows:
            rows = [
                score
                for score in db.query(Score).filter(Score.metadata_json.isnot(None), Score.name == "overall", Score.value.isnot(None)).all()
                if _score_dataset_id(score) == dataset_id
            ]
        grouped: dict[str, list[Score]] = defaultdict(list)
        for score in rows:
            item_id = _score_dataset_item_id(score)
            if item_id:
                grouped[item_id].append(score)

        items = []
        item_map = {
            item.dataset_item_id: item
            for item in db.query(DatasetItem)
            .filter(DatasetItem.dataset_id == dataset_id, DatasetItem.is_deleted.is_(False), DatasetItem.is_archived.is_(False))
            .all()
        }
        for item_id, scores in grouped.items():
            low_scores = [float(score.value) for score in scores if score.value is not None and float(score.value) < threshold]
            if not low_scores:
                continue
            item = item_map.get(item_id)
            items.append({
                "dataset_item_id": item_id,
                "source_trace_id": item.source_trace_id if item else None,
                "low_score_count": len(low_scores),
                "eval_count": len(scores),
                "avg_score": round(sum(float(score.value) for score in scores if score.value is not None) / len(scores), 2),
                "worst_score": round(min(low_scores), 2),
            })
        items.sort(key=lambda row: (-row["low_score_count"], row["worst_score"], row["dataset_item_id"]))
        return {"dataset_id": dataset_id, "threshold": threshold, "items": items[:limit], "total": len(items)}

    @staticmethod
    def experiment_report(db: Session, experiment_run_id: str, *, limit: int = 50) -> dict:
        run = db.query(ExperimentRun).filter(ExperimentRun.experiment_run_id == experiment_run_id).first()
        if run is None:
            raise HTTPException(status_code=404, detail="Experiment run not found")

        stats = EvaluationAnalyticsService.experiment_score_stats(db, experiment_run_id)
        scores = (
            db.query(Score)
            .filter(Score.experiment_run_id == experiment_run_id, Score.value.isnot(None))
            .all()
        )
        if not scores:
            scores = [
                score
                for score in db.query(Score).filter(Score.metadata_json.isnot(None), Score.value.isnot(None)).all()
                if _score_experiment_run_id(score) == experiment_run_id
            ]
        by_item: dict[str, dict] = defaultdict(lambda: {"dimension_scores": {}})
        for score in scores:
            item_id = _score_dataset_item_id(score)
            if not item_id:
                continue
            entry = by_item[item_id]
            entry["dataset_item_id"] = item_id
            if score.name == "overall":
                entry["overall"] = float(score.value)
            elif score.name in DIMENSION_NAMES:
                entry["dimension_scores"][score.name] = float(score.value)

        experiment_items = {
            item.dataset_item_id: item
            for item in db.query(ExperimentRunItem)
            .filter(ExperimentRunItem.experiment_run_id == experiment_run_id)
            .all()
        }
        worst_items = []
        for item_id, entry in by_item.items():
            item = experiment_items.get(item_id)
            worst_items.append({
                **entry,
                "experiment_item_id": item.experiment_item_id if item else None,
                "status": item.status if item else None,
                "trace_id": item.trace_id if item else None,
                "eval_run_id": item.eval_run_id if item else None,
            })
        worst_items.sort(key=lambda row: (row.get("overall") is None, row.get("overall", 999), row["dataset_item_id"]))
        return {
            "experiment_run_id": experiment_run_id,
            "dataset_id": run.dataset_id,
            "stats": stats,
            "worst_items": worst_items[:limit],
            "total_items": len(experiment_items),
        }

    @staticmethod
    def experiment_comparison_report(db: Session, left: str, right: str, *, limit: int = 50) -> dict:
        comparison = ExperimentRunService.compare_runs(db, left, right)
        dimension_deltas = comparison.get("dimension_deltas") or {}
        worst_dimension_deltas = sorted(
            [
                {"name": name, **values}
                for name, values in dimension_deltas.items()
                if values.get("delta") is not None
            ],
            key=lambda row: row["delta"],
        )
        regressed_items = [item for item in comparison.get("items", []) if item.get("status") == "regressed"]
        improved_items = [item for item in comparison.get("items", []) if item.get("status") == "improved"]
        return {
            "left": left,
            "right": right,
            "summary": {
                "compared_item_count": comparison.get("compared_item_count", 0),
                "improved_count": comparison.get("improved_count", 0),
                "regressed_count": comparison.get("regressed_count", 0),
                "neutral_count": comparison.get("neutral_count", 0),
            },
            "worst_dimension_deltas": worst_dimension_deltas[:limit],
            "regressed_items": regressed_items[:limit],
            "improved_items": improved_items[:limit],
        }
