"""Repositories for normalized Trace System v2 tables."""
from __future__ import annotations
from utils import new_id

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from db import Observation, Score, ScoreConfig, Trace, TraceV2


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _json_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def _json_obj(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return value
    return value


def _dt(value: Any) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
        except Exception:
            return None
    return None


def _int_from(*values: Any) -> int | None:
    for value in values:
        if value is None:
            continue
        try:
            return int(value)
        except Exception:
            continue
    return None


def _float_from(*values: Any) -> float | None:
    for value in values:
        if value is None:
            continue
        try:
            return float(value)
        except Exception:
            continue
    return None


DEFAULT_SCORE_CONFIGS: dict[str, dict[str, Any]] = {
    "overall": {"data_type": "NUMERIC", "min_value": 0.0, "max_value": 5.0, "description": "Overall answer quality."},
    "grounding": {"data_type": "NUMERIC", "min_value": 0.0, "max_value": 5.0, "description": "Evidence grounding quality."},
    "task_fit": {"data_type": "NUMERIC", "min_value": 0.0, "max_value": 5.0, "description": "Fit to the requested task."},
    "completeness": {"data_type": "NUMERIC", "min_value": 0.0, "max_value": 5.0, "description": "Completeness of the answer."},
    "specificity": {"data_type": "NUMERIC", "min_value": 0.0, "max_value": 5.0, "description": "Specificity and concreteness."},
    "source_quality": {"data_type": "NUMERIC", "min_value": 0.0, "max_value": 5.0, "description": "Quality of cited or retrieved sources."},
    "uncertainty_honesty": {"data_type": "NUMERIC", "min_value": 0.0, "max_value": 5.0, "description": "Appropriate uncertainty and caveats."},
    "format_fit": {"data_type": "NUMERIC", "min_value": 0.0, "max_value": 5.0, "description": "Fit to requested output format."},
    "verdict": {
        "data_type": "TEXT",
        "description": "Free-text quality verdict from evaluator.",
    },
    "feedback": {"data_type": "TEXT", "description": "Free-form evaluator or annotator feedback."},
}


# USD per 1M tokens — update when pricing changes
# Keys are substrings matched against the deployment/model name (case-insensitive, longest match wins)
_MODEL_PRICING: dict[str, tuple[float, float]] = {
    # (input $/1M, output $/1M)
    "o3-mini":           (1.10,   4.40),
    "o1-mini":           (3.00,  12.00),
    "o1":                (15.00, 60.00),
    "gpt-4o-mini":       (0.15,   0.60),
    "gpt-4o":            (2.50,  10.00),
    "gpt-4-turbo":       (10.00, 30.00),
    "gpt-4-32k":         (60.00, 120.00),
    "gpt-4":             (30.00, 60.00),
    "gpt-35-turbo-16k":  (3.00,   4.00),
    "gpt-35-turbo":      (0.50,   1.50),
    "gpt-3.5-turbo":     (0.50,   1.50),
    "text-embedding-3-large": (0.13, 0.13),
    "text-embedding-3-small": (0.02, 0.02),
    "text-embedding-ada": (0.10, 0.10),
}


def _lookup_price(model: str | None) -> tuple[float, float] | None:
    if not model:
        return None
    lower = model.lower()
    # Longest matching key wins (more specific models take precedence)
    best = max(
        ((k, v) for k, v in _MODEL_PRICING.items() if k in lower),
        key=lambda kv: len(kv[0]),
        default=None,
    )
    return best[1] if best else None


def _normalize_observation_type(value: Any) -> str:
    raw = str(value or "SPAN").upper()
    mapping = {
        "LLM": "GENERATION",
        "GENERATION": "GENERATION",
        "TOOL": "TOOL",
        "CHAIN": "CHAIN",
        "RETRIEVER": "RETRIEVER",
        "EVALUATOR": "EVALUATOR",
        "SPAN": "SPAN",
        "EVENT": "EVENT",
    }
    return mapping.get(raw, raw)


class TraceRepository:
    @staticmethod
    def upsert_trace(db: Session, body: dict) -> TraceV2:
        trace_id = body["trace_id"]
        row = db.query(TraceV2).filter(TraceV2.trace_id == trace_id).first()
        if row is None:
            row = TraceV2(trace_id=trace_id, name=body.get("name") or "trace", start_time=_dt(body.get("start_time")) or _utcnow())
            db.add(row)

        row.name = body.get("name") or row.name or "trace"
        row.thread_id = body.get("thread_id")
        row.user_id = body.get("user_id")
        row.environment = body.get("environment") or "default"
        # Only overwrite input/output if explicitly provided — preserves values from earlier upsert
        if body.get("input") is not None:
            row.input = _json_text(body["input"])
        if body.get("output") is not None:
            row.output = _json_text(body["output"])
        row.metadata_json = _json_text(body.get("metadata"))
        row.tags = _json_text(body.get("tags") or [])
        row.start_time = _dt(body.get("start_time")) or row.start_time or _utcnow()
        row.end_time = _dt(body.get("end_time"))
        row.updated_at = _utcnow()
        return row

    @staticmethod
    def get_trace(db: Session, trace_id: str) -> TraceV2 | None:
        return db.query(TraceV2).filter(TraceV2.trace_id == trace_id).first()


class ObservationRepository:
    @staticmethod
    def upsert_observation(db: Session, body: dict) -> Observation:
        observation_id = body["observation_id"]
        row = db.query(Observation).filter(Observation.observation_id == observation_id).first()
        if row is None:
            row = Observation(
                observation_id=observation_id,
                trace_id=body["trace_id"],
                type=_normalize_observation_type(body.get("type")),
                name=body.get("name") or "observation",
                start_time=_dt(body.get("start_time")) or _utcnow(),
            )
            db.add(row)

        row.trace_id = body.get("trace_id") or row.trace_id
        row.thread_id = body.get("thread_id") or row.thread_id
        row.parent_observation_id = body.get("parent_observation_id")
        row.type = _normalize_observation_type(body.get("type") or row.type)
        row.name = body.get("name") or row.name or "observation"
        row.model = body.get("model")
        row.model_parameters = _json_text(body.get("model_parameters") or body.get("modelParameters"))
        row.usage = _json_text(body.get("usage"))
        row.cost = _json_text(body.get("cost"))
        usage = _json_obj(body.get("usage")) or {}
        cost = _json_obj(body.get("cost")) or {}
        row.prompt_tokens = _int_from(body.get("prompt_tokens"), usage.get("prompt_tokens"), usage.get("input_tokens"))
        row.completion_tokens = _int_from(body.get("completion_tokens"), usage.get("completion_tokens"), usage.get("output_tokens"))
        row.total_tokens = _int_from(
            body.get("total_tokens"),
            usage.get("total_tokens"),
            (row.prompt_tokens or 0) + (row.completion_tokens or 0)
            if row.prompt_tokens is not None or row.completion_tokens is not None
            else None,
        )
        row.input_cost = _float_from(body.get("input_cost"), cost.get("input_cost"))
        row.output_cost = _float_from(body.get("output_cost"), cost.get("output_cost"))
        row.total_cost = _float_from(
            body.get("total_cost"),
            cost.get("total_cost"),
            (row.input_cost or 0) + (row.output_cost or 0)
            if row.input_cost is not None or row.output_cost is not None
            else None,
        )
        # Auto-compute cost from pricing table if not provided and tokens + model are available
        if row.input_cost is None and row.output_cost is None:
            pricing = _lookup_price(row.model)
            if pricing and (row.prompt_tokens or row.completion_tokens):
                in_price, out_price = pricing
                row.input_cost = round((row.prompt_tokens or 0) * in_price / 1_000_000, 8)
                row.output_cost = round((row.completion_tokens or 0) * out_price / 1_000_000, 8)
                row.total_cost = round(row.input_cost + row.output_cost, 8)
        row.prompt_name = body.get("prompt_name")
        row.prompt_version = body.get("prompt_version")
        row.input = _json_text(body.get("input"))
        row.output = _json_text(body.get("output"))
        row.metadata_json = _json_text(body.get("metadata"))
        row.level = body.get("level") or ("ERROR" if body.get("status_message") else "DEFAULT")
        row.status = "error" if row.level == "ERROR" else "success"
        row.status_message = body.get("status_message")
        row.start_time = _dt(body.get("start_time")) or row.start_time or _utcnow()
        row.completion_start_time = _dt(body.get("completion_start_time"))
        row.end_time = _dt(body.get("end_time"))
        row.updated_at = _utcnow()
        return row

    @staticmethod
    def list_for_trace(db: Session, trace_id: str, *, limit: int = 200) -> list[Observation]:
        return (
            db.query(Observation)
            .filter(Observation.trace_id == trace_id)
            .order_by(Observation.start_time.asc())
            .limit(limit)
            .all()
        )

    @staticmethod
    def list_recent(db: Session, *, limit: int = 100, offset: int = 0, run_type: str | None = None) -> list[Observation]:
        q = db.query(Observation)
        if run_type and run_type != "all":
            q = q.filter(Observation.type == run_type)
        return q.order_by(Observation.start_time.desc()).offset(offset).limit(limit).all()


class ScoreRepository:
    @staticmethod
    def ensure_config(db: Session, *, name: str, data_type: str = "NUMERIC") -> ScoreConfig:
        def _apply_defaults(row: ScoreConfig, default: dict | None) -> None:
            if not default:
                return
            row.data_type = default["data_type"]
            if row.min_value is None and default.get("min_value") is not None:
                row.min_value = default["min_value"]
            if row.max_value is None and default.get("max_value") is not None:
                row.max_value = default["max_value"]
            # Only set categories if defined in default; clear if default has none (e.g. TEXT type)
            if default.get("categories") is not None:
                row.categories = _json_text(default["categories"])
            elif default.get("data_type") in ("TEXT", "NUMERIC", "BOOLEAN"):
                row.categories = None
            if not row.description and default.get("description"):
                row.description = default["description"]

        row = db.query(ScoreConfig).filter(ScoreConfig.name == name).first()
        if row is not None and not isinstance(row, ScoreConfig):
            row = None
        default = DEFAULT_SCORE_CONFIGS.get(name)

        if row is not None:
            _apply_defaults(row, default)
            return row

        spec = default or {"data_type": data_type}
        row = ScoreConfig(
            name=name,
            data_type=spec.get("data_type") or data_type,
            min_value=spec.get("min_value"),
            max_value=spec.get("max_value"),
            categories=_json_text(spec.get("categories")) if spec.get("categories") is not None else None,
            description=spec.get("description"),
        )
        if hasattr(db, "add"):
            try:
                db.add(row)
                db.flush()
            except IntegrityError:
                # Concurrent worker already inserted this config; re-fetch and apply defaults
                db.rollback()
                row = db.query(ScoreConfig).filter(ScoreConfig.name == name).first()
                _apply_defaults(row, default)
        return row

    @staticmethod
    def seed_default_configs(db: Session) -> list[ScoreConfig]:
        rows = []
        for name, spec in DEFAULT_SCORE_CONFIGS.items():
            rows.append(ScoreRepository.ensure_config(db, name=name, data_type=spec["data_type"]))
        if hasattr(db, "flush"):
            db.flush()
        return rows

    @staticmethod
    def _validate_payload(config: ScoreConfig, body: dict) -> str:
        config_type = (config.data_type or body.get("data_type") or "NUMERIC").upper()
        name = body.get("name")
        value = body.get("value")
        string_value = body.get("string_value")

        if config_type == "NUMERIC":
            if value is None:
                raise ValueError(f"Score {name!r} requires numeric value")
            try:
                numeric_value = float(value)
            except Exception as exc:
                raise ValueError(f"Score {name!r} value must be numeric") from exc
            if config.min_value is not None and numeric_value < float(config.min_value):
                raise ValueError(f"Score {name!r} value {numeric_value} is below minimum {config.min_value}")
            if config.max_value is not None and numeric_value > float(config.max_value):
                raise ValueError(f"Score {name!r} value {numeric_value} is above maximum {config.max_value}")
        elif config_type == "CATEGORICAL":
            if not string_value:
                raise ValueError(f"Score {name!r} requires string_value")
            categories = _json_obj(config.categories) or []
            if categories and string_value not in categories:
                raise ValueError(f"Score {name!r} string_value must be one of {categories}")
        elif config_type == "BOOLEAN":
            if value is None and string_value is None:
                raise ValueError(f"Score {name!r} requires boolean value")
            raw = string_value if string_value is not None else value
            if str(raw).lower() not in {"true", "false", "1", "0"}:
                raise ValueError(f"Score {name!r} value must be boolean")
        elif config_type == "TEXT":
            if not string_value and not body.get("comment"):
                raise ValueError(f"Score {name!r} requires string_value or comment")
        else:
            raise ValueError(f"Unsupported score data_type {config_type!r} for {name!r}")
        return config_type

    @staticmethod
    def upsert_score(db: Session, body: dict, *, sync_legacy_cache: bool = True) -> Score:
        name = body["name"]
        data_type = body.get("data_type") or "NUMERIC"
        config = ScoreRepository.ensure_config(db, name=name, data_type=data_type)
        if hasattr(db, "flush"):
            db.flush()
        data_type = ScoreRepository._validate_payload(config, body)
        metadata = body.get("metadata") or {}
        if not isinstance(metadata, dict):
            metadata = {}

        score_id = body.get("score_id") or new_id()
        row = db.query(Score).filter(Score.score_id == score_id).first()
        if not isinstance(row, Score):
            row = None
        if row is None:
            row = Score(score_id=score_id, name=name)
            if hasattr(db, "add"):
                db.add(row)

        row.trace_id = body.get("trace_id")
        row.observation_id = body.get("observation_id")
        row.name = name
        row.value = body.get("value")
        row.string_value = body.get("string_value")
        row.data_type = data_type
        row.source = body.get("source") or "API"
        row.comment = body.get("comment")
        row.metadata_json = _json_text(body.get("metadata"))
        row.execution_trace_id = body.get("execution_trace_id")
        row.eval_run_id = body.get("eval_run_id") or metadata.get("eval_run_id") or row.execution_trace_id
        row.eval_item_id = body.get("eval_item_id") or metadata.get("eval_item_id")
        row.dataset_id = body.get("dataset_id") or metadata.get("dataset_id")
        row.dataset_item_id = body.get("dataset_item_id") or metadata.get("dataset_item_id")
        row.experiment_run_id = body.get("experiment_run_id") or metadata.get("experiment_run_id")
        row.experiment_item_id = body.get("experiment_item_id") or metadata.get("experiment_item_id")
        row.score_config_id = getattr(config, "id", None)
        row.timestamp = body.get("timestamp") or getattr(row, "timestamp", None) or _utcnow()
        row.updated_at = _utcnow()

        if sync_legacy_cache and row.trace_id:
            ScoreRepository.sync_legacy_trace_cache(db, row.trace_id)
        return row

    @staticmethod
    def sync_legacy_trace_cache(db: Session, trace_id: str) -> None:
        trace = db.query(Trace).filter(Trace.observation_id == trace_id).first()
        if trace is None:
            return

        scores = db.query(Score).filter(Score.trace_id == trace_id).all()
        numeric = {s.name: s.value for s in scores if s.value is not None}
        comments = [s.comment for s in scores if s.comment]

        if "overall" in numeric:
            trace.quality_score = numeric["overall"]
        elif numeric:
            trace.quality_score = sum(numeric.values()) / len(numeric)

        if numeric:
            detail = dict(numeric)
            verdict = next((s.string_value for s in scores if s.name == "verdict" and s.string_value), None)
            if verdict:
                detail["verdict"] = verdict
            trace.quality_detail = json.dumps(detail, ensure_ascii=False)
        if comments and not trace.user_feedback:
            trace.user_feedback = comments[-1][:500]

    @staticmethod
    def score_stats(db: Session) -> dict | None:
        dim_names = ["grounding", "task_fit", "completeness", "specificity",
                     "source_quality", "uncertainty_honesty", "format_fit"]
        all_names = dim_names + ["overall"]

        # 單條 GROUP BY 取代 N+1 查詢
        rows = (
            db.query(Score.name, func.avg(Score.value), func.count(Score.score_id))
            .filter(Score.name.in_(all_names), Score.value.isnot(None))
            .group_by(Score.name)
            .all()
        )
        agg = {name: {"avg": avg, "count": cnt} for name, avg, cnt in rows}

        if "overall" not in agg or agg["overall"]["count"] == 0:
            return None

        # overall 分數列表（仍需個別值做 distribution / trace_id set）
        overall_scores = (
            db.query(Score.value, Score.trace_id)
            .filter(Score.name == "overall", Score.value.isnot(None))
            .all()
        )
        trace_ids = {tid for _, tid in overall_scores if tid}
        all_trace_count = db.query(func.count(TraceV2.trace_id)).scalar() or len(trace_ids)
        scored = len(trace_ids)
        unscored = max(all_trace_count - scored, 0)

        buckets = {"0-1": 0, "1-2": 0, "2-3": 0, "3-4": 0, "4-5": 0}
        vals = []
        for value, _ in overall_scores:
            v = float(value)
            vals.append(v)
            if v < 1:   buckets["0-1"] += 1
            elif v < 2: buckets["1-2"] += 1
            elif v < 3: buckets["2-3"] += 1
            elif v < 4: buckets["3-4"] += 1
            else:       buckets["4-5"] += 1

        low = sum(1 for v in vals if v < 3)
        dimension_avgs = {
            name: round(float(agg[name]["avg"]), 2) if name in agg else None
            for name in dim_names
        }
        return {
            "total": all_trace_count,
            "scored": scored,
            "unscored": unscored,
            "avg_score": round(sum(vals) / len(vals), 2) if vals else None,
            "low_quality_count": low,
            "low_quality_pct": round(low / len(vals) * 100, 1) if vals else None,
            "distribution": [{"bucket": k, "count": v} for k, v in buckets.items()],
            "dimension_avgs": dimension_avgs,
        }


def observation_to_trace_payload(obs: Observation) -> dict:
    usage = _json_obj(obs.usage) or {}
    return {
        "id": obs.observation_id,
        "run_type": obs.type,
        "name": obs.name,
        "parent_observation_id": obs.parent_observation_id,
        "thread_id": None,
        "start_time": obs.start_time.isoformat() + "Z" if obs.start_time else None,
        "end_time": obs.end_time.isoformat() + "Z" if obs.end_time else None,
        "latency": round((obs.end_time - obs.start_time).total_seconds(), 2) if obs.start_time and obs.end_time else None,
        "prompt_tokens": usage.get("input") or usage.get("prompt_tokens"),
        "completion_tokens": usage.get("output") or usage.get("completion_tokens"),
        "error": obs.status_message if obs.level == "ERROR" else None,
        "input": str(_json_obj(obs.input) or "")[:120] if obs.input else None,
        "output": str(_json_obj(obs.output) or "")[:120] if obs.output else None,
        "inputs_raw": _json_obj(obs.input),
        "outputs_raw": _json_obj(obs.output),
    }
