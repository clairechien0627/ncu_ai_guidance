"""Statistics and observation read operations for traces."""
from __future__ import annotations

from services.trace_read_common import *  # noqa: F401,F403
from services.trace_read_core import _TraceReadCore
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
        payloads = self.core._merged_payloads({}, include_display=False)
        if payloads:
            total = len(payloads)
            cur_rows = [p for p in payloads if p.get("start_time") and datetime.fromisoformat(p["start_time"].replace("Z", "+00:00")).replace(tzinfo=None) >= cur_start]
            prev_rows = [p for p in payloads if p.get("start_time") and prev_start <= datetime.fromisoformat(p["start_time"].replace("Z", "+00:00")).replace(tzinfo=None) < cur_start]

            def agg(items):
                lats = [p["latency"] for p in items if p["latency"] is not None]
                qs = [p["quality_score"] for p in items if p["quality_score"] is not None]
                errs = sum(1 for p in items if p["status"] == "error")
                return {
                    "runs": len(items),
                    "errors": errs,
                    "error_rate": round(errs / len(items) * 100, 1) if items else 0.0,
                    "avg_latency": round(sum(lats) / len(lats), 2) if lats else None,
                    "avg_quality": round(sum(qs) / len(qs), 2) if qs else None,
                }
            return self.core._stats_response(total, agg(cur_rows), agg(prev_rows))

        legacy_rows = self.core._legacy_filtered({}, limit=100000, offset=0)
        total = len(legacy_rows)
        cur = self.core._legacy_agg([t for t in legacy_rows if t.start_time and t.start_time >= cur_start])
        prev = self.core._legacy_agg([t for t in legacy_rows if t.start_time and prev_start <= t.start_time < cur_start])
        return self.core._stats_response(total, cur, prev)


    def grouped(self, field: str) -> list[dict]:
        groups: dict[str, dict] = {}
        for payload in self.core._merged_payloads({}, include_display=False, legacy_limit=1000):
            key = (
                f"{payload.get('prompt_name') or 'unknown'}@{payload.get('prompt_version') or 'unknown'}"
                if field == "prompt_version"
                else payload.get(field) or "unknown"
            )
            item = groups.setdefault(key, {"key": key, "runs": 0, "errors": 0, "latencies": [], "tokens": 0, "quality_scores": [], "feedback_count": 0})
            item["runs"] += 1
            item["errors"] += 1 if payload["status"] == "error" else 0
            if payload.get("latency") is not None:
                item["latencies"].append(payload["latency"])
            item["tokens"] += (payload.get("prompt_tokens") or 0) + (payload.get("completion_tokens") or 0)
            if payload.get("quality_score") is not None:
                item["quality_scores"].append(payload["quality_score"])
            if payload.get("user_feedback"):
                item["feedback_count"] += 1
        return self.core._finish_groups(groups)


    def observations(self, *, limit: int = 100, offset: int = 0, run_type: str | None = None) -> list[dict]:
        q = self.core.db.query(Observation)
        if run_type and run_type != "all":
            q = q.filter(Observation.type == run_type)
        rows = q.order_by(Observation.start_time.desc()).offset(offset).limit(limit).all()
        if rows:
            return [observation_to_trace_payload(row) for row in rows]
        legacy_q = self.core.db.query(Trace).filter(Trace.trace_id.isnot(None))
        if run_type and run_type != "all":
            legacy_q = legacy_q.filter(Trace.run_type == run_type)
        return [
            {
                "id": t.observation_id,
                "run_type": t.run_type,
                "name": t.name,
                "trace_id": t.trace_id,
                "thread_id": t.thread_id,
                "start_time": t.start_time.isoformat() + "Z" if t.start_time else None,
                "latency": _legacy_latency(t),
                "prompt_tokens": t.prompt_tokens,
                "completion_tokens": t.completion_tokens,
                "error": t.error,
                "input": _truncate(t.inputs, 120),
                "output": _truncate(t.outputs, 120),
            }
            for t in legacy_q.order_by(Trace.start_time.desc()).offset(offset).limit(limit).all()
        ]


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

        if self.core.db.query(Observation.id).first():
            rows = self.core.db.query(Observation).all()
            return _agg_rows(rows,
                type_fn=lambda o: o.type,
                pt_fn=lambda o: o.prompt_tokens,
                ct_fn=lambda o: o.completion_tokens,
            )
        rows = self.core.db.query(Trace).filter(Trace.trace_id.isnot(None)).all()
        return _agg_rows(rows,
            type_fn=lambda t: t.run_type,
            pt_fn=lambda t: t.prompt_tokens,
            ct_fn=lambda t: t.completion_tokens,
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

        payloads = self.core._merged_payloads({}, include_display=False)
        if payloads:
            values = [float(p["quality_score"]) for p in payloads if p.get("quality_score") is not None]
            # Dimension averages come from the scores table (v2 source of truth).
            # Only fall back to legacy quality_detail for traces not yet in v2.
            dim_values: dict[str, list[float]] = {name: [] for name in QUALITY_DIMENSIONS}
            for score in self.core.db.query(Score).filter(Score.name.in_(QUALITY_DIMENSIONS), Score.value.isnot(None)).all():
                dim_values.setdefault(score.name, []).append(float(score.value))
            if not self.core.has_v2():
                for trace in self.core._legacy_filtered({}, limit=100000, offset=0):
                    detail = _legacy_parse_quality(trace.quality_detail)
                    if not isinstance(detail, dict):
                        continue
                    for name in QUALITY_DIMENSIONS:
                        v = detail.get(name)
                        if isinstance(v, (int, float)):
                            dim_values.setdefault(name, []).append(float(v))
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

        legacy_rows = self.core._legacy_filtered({}, limit=100000, offset=0)
        scored = [t for t in legacy_rows if t.quality_score is not None]
        values = [float(t.quality_score) for t in scored]
        dim_values = self.core._legacy_dimension_avgs(scored)
        result = _summary(len(legacy_rows), values, {k: [] for k in QUALITY_DIMENSIONS})
        result["dimension_avgs"] = dim_values
        return result


    def timeline(self, *, prompt_name: str | None = None, task_type: str | None = None, route_intent: str | None = None, days: int = 14) -> list[dict]:
        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
        payloads = [
            p for p in self.core._merged_payloads(
                {"prompt_name": prompt_name, "task_type": task_type, "route_intent": route_intent},
                include_display=False,
            )
            if p.get("start_time") and datetime.fromisoformat(p["start_time"].replace("Z", "+00:00")).replace(tzinfo=None) >= cutoff
        ]
        return self.core._timeline_from_payloads(payloads)

