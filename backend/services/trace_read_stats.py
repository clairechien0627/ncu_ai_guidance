"""Statistics and observation read operations for traces."""
from __future__ import annotations

from services.trace_read_common import *  # noqa: F401,F403
from services.trace_read_core import _TraceReadCore
from services.trace_repositories import observation_to_trace_payload


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
        legacy_q = self.core.db.query(Trace).filter(Trace.parent_run_id.isnot(None))
        if run_type and run_type != "all":
            legacy_q = legacy_q.filter(Trace.run_type == run_type)
        return [
            {
                "id": t.run_id,
                "run_type": t.run_type,
                "name": t.name,
                "parent_run_id": t.parent_run_id,
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
        if self.core.db.query(Observation.id).first():
            by_type: dict[str, dict] = {}
            total_tokens = 0
            for obs in self.core.db.query(Observation).all():
                item = by_type.setdefault(obs.type, {"count": 0, "prompt_tokens": 0, "completion_tokens": 0})
                usage = _parse_json(obs.usage) or {}
                item["count"] += 1
                prompt_tokens = 0
                completion_tokens = 0
                if isinstance(usage, dict):
                    prompt_tokens = int(usage.get("input") or usage.get("prompt_tokens") or 0)
                    completion_tokens = int(usage.get("output") or usage.get("completion_tokens") or 0)
                    item["prompt_tokens"] += prompt_tokens
                    item["completion_tokens"] += completion_tokens
                total_tokens += prompt_tokens + completion_tokens
            return {"total": sum(v["count"] for v in by_type.values()), "by_type": by_type, "total_tokens": total_tokens}
        rows = self.core.db.query(Trace).filter(Trace.parent_run_id.isnot(None)).all()
        by_type: dict[str, dict] = {}
        for t in rows:
            item = by_type.setdefault(t.run_type, {"count": 0, "prompt_tokens": 0, "completion_tokens": 0})
            item["count"] += 1
            item["prompt_tokens"] += t.prompt_tokens or 0
            item["completion_tokens"] += t.completion_tokens or 0
        return {"total": len(rows), "by_type": by_type, "total_tokens": sum(v["prompt_tokens"] + v["completion_tokens"] for v in by_type.values())}


    def score_stats(self) -> dict:
        payloads = self.core._merged_payloads({}, include_display=False)
        if payloads:
            values = [float(p["quality_score"]) for p in payloads if p.get("quality_score") is not None]
            buckets = {"0-1": 0, "1-2": 0, "2-3": 0, "3-4": 0, "4-5": 0}
            for value in values:
                if value < 1: buckets["0-1"] += 1
                elif value < 2: buckets["1-2"] += 1
                elif value < 3: buckets["2-3"] += 1
                elif value < 4: buckets["3-4"] += 1
                else: buckets["4-5"] += 1

            dim_values: dict[str, list[float]] = {name: [] for name in QUALITY_DIMENSIONS}
            for score in self.core.db.query(Score).filter(Score.name.in_(QUALITY_DIMENSIONS), Score.value.isnot(None)).all():
                dim_values.setdefault(score.name, []).append(float(score.value))

            v2_ids = {tid for (tid,) in self.core.db.query(TraceV2.trace_id).all() if tid}
            for trace in self.core._legacy_filtered({}, limit=100000, offset=0):
                if trace.run_id in v2_ids:
                    continue
                detail = _legacy_parse_quality(trace.quality_detail)
                if not isinstance(detail, dict):
                    continue
                for name in QUALITY_DIMENSIONS:
                    value = detail.get(name)
                    if isinstance(value, (int, float)):
                        dim_values.setdefault(name, []).append(float(value))

            low = sum(1 for v in values if v < 3)
            return {
                "total": len(payloads),
                "scored": len(values),
                "unscored": len(payloads) - len(values),
                "avg_score": round(sum(values) / len(values), 2) if values else None,
                "low_quality_count": low,
                "low_quality_pct": round(low / len(values) * 100, 1) if values else None,
                "distribution": [{"bucket": k, "count": v} for k, v in buckets.items()],
                "dimension_avgs": {
                    name: round(sum(vals) / len(vals), 2) if vals else None
                    for name, vals in dim_values.items()
                },
            }

        rows = self.core.db.query(Score).all()
        if rows:
            trace_ids = {r.trace_id for r in rows if r.trace_id}
            all_trace_count = self.core.db.query(TraceV2).count() or len(trace_ids)
            overall = [r for r in rows if r.name == "overall" and r.value is not None]
            values = [float(r.value) for r in overall]
            buckets = {"0-1": 0, "1-2": 0, "2-3": 0, "3-4": 0, "4-5": 0}
            for value in values:
                if value < 1: buckets["0-1"] += 1
                elif value < 2: buckets["1-2"] += 1
                elif value < 3: buckets["2-3"] += 1
                elif value < 4: buckets["3-4"] += 1
                else: buckets["4-5"] += 1
            dim_avgs = {}
            for name in QUALITY_DIMENSIONS:
                vals = [float(r.value) for r in rows if r.name == name and r.value is not None]
                dim_avgs[name] = round(sum(vals) / len(vals), 2) if vals else None
            low = sum(1 for v in values if v < 3)
            return {
                "total": all_trace_count,
                "scored": len({r.trace_id for r in overall if r.trace_id}),
                "unscored": max(all_trace_count - len({r.trace_id for r in overall if r.trace_id}), 0),
                "avg_score": round(sum(values) / len(values), 2) if values else None,
                "low_quality_count": low,
                "low_quality_pct": round(low / len(values) * 100, 1) if values else None,
                "distribution": [{"bucket": k, "count": v} for k, v in buckets.items()],
                "dimension_avgs": dim_avgs,
            }
        legacy_rows = self.core._legacy_filtered({}, limit=100000, offset=0)
        scored = [t for t in legacy_rows if t.quality_score is not None]
        buckets = {"0-1": 0, "1-2": 0, "2-3": 0, "3-4": 0, "4-5": 0}
        for t in scored:
            s = t.quality_score
            if s < 1: buckets["0-1"] += 1
            elif s < 2: buckets["1-2"] += 1
            elif s < 3: buckets["2-3"] += 1
            elif s < 4: buckets["3-4"] += 1
            else: buckets["4-5"] += 1
        low = sum(1 for t in scored if t.quality_score < 3)
        return {
            "total": len(legacy_rows),
            "scored": len(scored),
            "unscored": len(legacy_rows) - len(scored),
            "avg_score": round(sum(t.quality_score for t in scored) / len(scored), 2) if scored else None,
            "low_quality_count": low,
            "low_quality_pct": round(low / len(scored) * 100, 1) if scored else None,
            "distribution": [{"bucket": k, "count": v} for k, v in buckets.items()],
            "dimension_avgs": self.core._legacy_dimension_avgs(scored),
        }


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

