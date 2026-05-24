"""Statistics and observation read operations for traces."""
from __future__ import annotations

from .common import *  # noqa: F401,F403
from .core import _TraceReadCore
from services.trace_repositories import observation_to_trace_payload


def _score_buckets(values: list[float]) -> dict[str, int]:
    buckets: dict[str, int] = {"0-1": 0, "1-2": 0, "2-3": 0, "3-4": 0, "4-5": 0}
    for v in values:
        if v < 1:   buckets["0-1"] += 1
        elif v < 2: buckets["1-2"] += 1
        elif v < 3: buckets["2-3"] += 1
        elif v < 4: buckets["3-4"] += 1
        else:       buckets["4-5"] += 1
    return buckets


class TraceStatsReadService:
    def __init__(self, core: _TraceReadCore):
        self.core = core

    def stats(self, *, days: int = 7) -> dict:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        cur_start = now - timedelta(days=days)
        prev_start = now - timedelta(days=days * 2)
        payloads = self.core._merged_payloads({})
        total = len(payloads)
        cur_rows = [p for p in payloads if p.get("start_time") and datetime.fromisoformat(p["start_time"].replace("Z", "+00:00")).replace(tzinfo=None) >= cur_start]
        prev_rows = [p for p in payloads if p.get("start_time") and prev_start <= datetime.fromisoformat(p["start_time"].replace("Z", "+00:00")).replace(tzinfo=None) < cur_start]

        def agg(items):
            lats = [p["latency"] for p in items if p["latency"] is not None]
            qs = [p["quality_score"] for p in items if p["quality_score"] is not None]
            errs = sum(1 for p in items if p["status"] == "ERROR")
            return {
                "runs": len(items),
                "errors": errs,
                "error_rate": round(errs / len(items) * 100, 1) if items else 0.0,
                "avg_latency": round(sum(lats) / len(lats), 2) if lats else None,
                "avg_quality": round(sum(qs) / len(qs), 2) if qs else None,
            }
        return self.core._stats_response(total, agg(cur_rows), agg(prev_rows))


    def grouped(self, field: str) -> list[dict]:
        groups: dict[str, dict] = {}
        if field in {"prompt_name", "prompt_version"}:
            payloads = {p["id"]: p for p in self.core._merged_payloads({})}
            seen: set[tuple[str, str]] = set()
            rows = (
                self.core.db.query(Observation.trace_id, Observation.prompt_name, Observation.prompt_version)
                .filter(Observation.prompt_name.isnot(None) if field == "prompt_name" else Observation.prompt_version.isnot(None))
                .distinct()
                .all()
            )
            for trace_id, prompt_name, prompt_version in rows:
                payload = payloads.get(trace_id)
                if not payload:
                    continue
                key = (
                    f"{prompt_name or 'unknown'}@{prompt_version or 'unknown'}"
                    if field == "prompt_version"
                    else prompt_name or "unknown"
                )
                if (trace_id, key) in seen:
                    continue
                seen.add((trace_id, key))
                item = groups.setdefault(key, {"key": key, "runs": 0, "errors": 0, "latencies": [], "tokens": 0, "quality_scores": [], "feedback_count": 0})
                self._add_group_payload(item, payload)
        else:
            for payload in self.core._merged_payloads({}):
                key = payload.get(field) or "unknown"
                item = groups.setdefault(key, {"key": key, "runs": 0, "errors": 0, "latencies": [], "tokens": 0, "quality_scores": [], "feedback_count": 0})
                self._add_group_payload(item, payload)
        return self.core._finish_groups(groups)

    @staticmethod
    def _add_group_payload(item: dict, payload: dict) -> None:
        item["runs"] += 1
        item["errors"] += 1 if payload["status"] == "ERROR" else 0
        if payload.get("latency") is not None:
            item["latencies"].append(payload["latency"])
        item["tokens"] += (payload.get("prompt_tokens") or 0) + (payload.get("completion_tokens") or 0)
        if payload.get("quality_score") is not None:
            item["quality_scores"].append(payload["quality_score"])
        if payload.get("user_feedback"):
            item["feedback_count"] += 1


    def observations(self, *, limit: int = 100, offset: int = 0, obs_type: str | None = None) -> list[dict]:
        q = self.core.db.query(Observation)
        if obs_type and obs_type != "all":
            q = q.filter(Observation.type == obs_type)
        rows = q.order_by(Observation.start_time.desc()).offset(offset).limit(limit).all()
        return [observation_to_trace_payload(row) for row in rows]


    def observation_stats(self) -> dict:
        def _agg_rows(rows, type_fn, pt_fn, ct_fn) -> dict:
            by_type: dict[str, dict] = {}
            for row in rows:
                item = by_type.setdefault(type_fn(row), {"count": 0, "prompt_tokens": 0, "completion_tokens": 0})
                item["count"] += 1
                item["prompt_tokens"] += pt_fn(row) or 0
                item["completion_tokens"] += ct_fn(row) or 0
            total_tokens = sum(v["prompt_tokens"] + v["completion_tokens"] for v in by_type.values())
            return {"total": sum(v["count"] for v in by_type.values()), "by_type": by_type, "total_tokens": total_tokens}

        rows = self.core.db.query(Observation).all()
        return _agg_rows(rows,
            type_fn=lambda o: o.type,
            pt_fn=lambda o: o.prompt_tokens,
            ct_fn=lambda o: o.completion_tokens,
        )


    def score_stats(self) -> dict:
        def _dim_avgs(dim_values: dict[str, list[float]]) -> dict[str, float | None]:
            return {k: round(sum(v) / len(v), 2) if v else None for k, v in dim_values.items()}

        def _summary(total: int, values: list[float], dim_values: dict[str, list[float]]) -> dict:
            low = sum(1 for v in values if v < 3)
            return {
                "total": total,
                "scored": len(values),
                "unscored": max(total - len(values), 0),
                "avg_score": round(sum(values) / len(values), 2) if values else None,
                "low_quality_count": low,
                "low_quality_pct": round(low / len(values) * 100, 1) if values else None,
                "distribution": [{"bucket": k, "count": v} for k, v in _score_buckets(values).items()],
                "dimension_avgs": _dim_avgs(dim_values),
            }

        payloads = self.core._merged_payloads({})
        values = [float(p["quality_score"]) for p in payloads if p.get("quality_score") is not None]
        dim_values: dict[str, list[float]] = {name: [] for name in QUALITY_DIMENSIONS}
        for score in self.core.db.query(Score).filter(Score.name.in_(QUALITY_DIMENSIONS), Score.value.isnot(None)).all():
            dim_values.setdefault(score.name, []).append(float(score.value))
        if payloads:
            return _summary(len(payloads), values, dim_values)
        rows = self.core.db.query(Score).all()
        if rows:
            all_trace_count = self.core.db.query(TraceV2).count() or len({r.trace_id for r in rows if r.trace_id})
            overall = [r for r in rows if r.name == "overall" and r.value is not None]
            values = [float(r.value) for r in overall]
            dim_values = {
                name: [float(r.value) for r in rows if r.name == name and r.value is not None]
                for name in QUALITY_DIMENSIONS
            }
            scored_count = len({r.trace_id for r in overall if r.trace_id})
            result = _summary(all_trace_count, values, dim_values)
            result["scored"] = scored_count
            result["unscored"] = max(all_trace_count - scored_count, 0)
            return result
        return _summary(0, [], {k: [] for k in QUALITY_DIMENSIONS})


    def timeline(self, *, prompt_name: str | None = None, agent_name: str | None = None, days: int = 14) -> list[dict]:
        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
        payloads = [
            p for p in self.core._merged_payloads(
                {"prompt_name": prompt_name, "agent_name": agent_name},
            )
            if p.get("start_time") and datetime.fromisoformat(p["start_time"].replace("Z", "+00:00")).replace(tzinfo=None) >= cutoff
        ]
        return self.core._timeline_from_payloads(payloads)

