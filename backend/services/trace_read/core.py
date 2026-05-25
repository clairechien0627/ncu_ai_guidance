"""Internal core helpers for TraceReadService facade.

This module keeps shared Trace v2 helpers in one place while public read
capabilities live in smaller service classes.
"""
from __future__ import annotations

from .common import *  # noqa: F401,F403


class _TraceReadCore:
    def __init__(self, db: Session):
        self.db = db


    def has_v2(self) -> bool:
        return bool(self.db.query(TraceV2.id).first())


    def environments(self) -> list[str]:
        values = {row[0] for row in self.db.query(TraceV2.environment).distinct().all() if row[0]}
        return sorted(values)


    def _merged_payloads(
        self,
        filters: dict,
        *,
        include_raw: bool = False,
    ) -> list[dict]:
        payloads: list[dict] = []
        cleaned = {k: _clean(v) if isinstance(v, str) else v for k, v in filters.items()}
        if self.has_v2():
            v2_rows = self._v2_filtered(filters)
            if v2_rows:
                trace_ids = [r.trace_id for r in v2_rows]

                # Batch-load scores and observations — eliminates N+1 per trace
                all_scores = self.db.query(Score).filter(Score.trace_id.in_(trace_ids)).all()
                scores_by_trace: dict[str, list] = {}
                for s in all_scores:
                    if s.trace_id:
                        scores_by_trace.setdefault(s.trace_id, []).append(s)

                all_obs = self.db.query(Observation).filter(Observation.trace_id.in_(trace_ids)).all()
                obs_by_trace: dict[str, list] = {}
                for o in all_obs:
                    obs_by_trace.setdefault(o.trace_id, []).append(o)

                for row in v2_rows:
                    payload = _build_v2_payload(
                        row,
                        scores_by_trace.get(row.trace_id, []),
                        obs_by_trace.get(row.trace_id, []),
                        include_raw=include_raw,
                    )
                    if filters.get("status"):
                        status_list = [s.strip().upper() for s in str(filters["status"]).split(",") if s.strip()]
                        if status_list and (payload.get("status") or "DEFAULT") not in status_list:
                            continue
                    if cleaned.get("min_latency") is not None and (payload.get("latency") is None or payload["latency"] < float(cleaned["min_latency"])):
                        continue
                    if cleaned.get("max_quality") is not None and (payload.get("quality_score") is None or payload["quality_score"] >= float(cleaned["max_quality"])):
                        continue
                    if cleaned.get("min_quality") is not None and (payload.get("quality_score") is None or payload["quality_score"] < float(cleaned["min_quality"])):
                        continue
                    if cleaned.get("has_score") is True and payload.get("quality_score") is None:
                        continue
                    if cleaned.get("has_score") is False and payload.get("quality_score") is not None:
                        continue
                    if cleaned.get("min_tokens") is not None and (payload.get("total_tokens") or 0) < float(cleaned["min_tokens"]):
                        continue
                    if cleaned.get("max_tokens") is not None and (payload.get("total_tokens") or 0) > float(cleaned["max_tokens"]):
                        continue
                    if cleaned.get("min_input_tokens") is not None and (payload.get("prompt_tokens") or 0) < float(cleaned["min_input_tokens"]):
                        continue
                    if cleaned.get("min_output_tokens") is not None and (payload.get("completion_tokens") or 0) < float(cleaned["min_output_tokens"]):
                        continue
                    payloads.append(payload)
        return payloads


    def _v2_filtered(self, filters: dict) -> list[TraceV2]:
        """SQL + metadata-JSON filtering only. No payload building or extra queries."""
        q = self.db.query(TraceV2)
        if _clean(filters.get("environment")):
            q = q.filter(TraceV2.environment == _clean(filters.get("environment")))
        if filters.get("user_ids"):
            uid_list = [u.strip() for u in str(filters["user_ids"]).split(",") if u.strip()]
            if uid_list:
                q = q.filter(TraceV2.user_id.in_(uid_list))
        elif _clean(filters.get("user_id")):
            q = q.filter(TraceV2.user_id == _clean(filters.get("user_id")))
        if filters.get("names"):
            name_list = [n.strip() for n in str(filters["names"]).split(",") if n.strip()]
            if name_list:
                q = q.filter(TraceV2.name.in_(name_list))
        elif _clean(filters.get("name")):
            q = q.filter(TraceV2.name == _clean(filters.get("name")))
        if filters.get("bookmarked") is not None:
            bookmarked = filters.get("bookmarked")
            if isinstance(bookmarked, str):
                bookmarked = bookmarked.strip().lower() in {"1", "true", "yes", "on"}
            q = q.filter(TraceV2.bookmarked == bool(bookmarked))
        if filters.get("tags"):
            tag_list = [t.strip() for t in str(filters["tags"]).split(",") if t.strip()]
            if tag_list:
                from sqlalchemy import or_ as _or_tags
                q = q.filter(_or_tags(*[TraceV2.tags.contains(f'"{t}"') for t in tag_list]))
        for key, op in [("date_from", ">="), ("date_to", "<=")]:
            if filters.get(key):
                try:
                    dt = datetime.fromisoformat(filters[key])
                    q = q.filter(TraceV2.start_time >= dt) if op == ">=" else q.filter(TraceV2.start_time <= dt)
                except ValueError:
                    pass
        cleaned = {k: _clean(v) if isinstance(v, str) else v for k, v in filters.items()}
        if cleaned.get("prompt_name") or cleaned.get("prompt_version"):
            obs_q = self.db.query(Observation.trace_id)
            if cleaned.get("prompt_name"):
                obs_q = obs_q.filter(Observation.prompt_name == cleaned["prompt_name"])
            if cleaned.get("prompt_version"):
                obs_q = obs_q.filter(Observation.prompt_version == cleaned["prompt_version"])
            trace_ids = [row[0] for row in obs_q.distinct().all() if row[0]]
            if not trace_ids:
                return []
            q = q.filter(TraceV2.trace_id.in_(trace_ids))

        return q.order_by(TraceV2.start_time.desc()).all()


    @staticmethod

    def _finish_groups(groups: dict[str, dict]) -> list[dict]:
        result = []
        for item in groups.values():
            latencies = item.pop("latencies")
            quality_scores = item.pop("quality_scores")
            item["avg_latency"] = round(sum(latencies) / len(latencies), 2) if latencies else None
            item["avg_quality_score"] = round(sum(quality_scores) / len(quality_scores), 2) if quality_scores else None
            result.append(item)
        return sorted(result, key=lambda x: x["runs"], reverse=True)

    @staticmethod

    def _stats_response(total: int, cur: dict, prev: dict) -> dict:
        def trend(cur_val, prev_val):
            if cur_val is None or prev_val is None or prev_val == 0:
                return None
            return round((cur_val - prev_val) / prev_val * 100, 1)
        return {
            "total_runs": total,
            "period_runs": cur["runs"],
            "error_runs": cur["errors"],
            "success_runs": cur["runs"] - cur["errors"],
            "error_rate": cur["error_rate"],
            "avg_latency": cur["avg_latency"],
            "avg_quality": cur["avg_quality"],
            "runs_trend": trend(cur["runs"], prev["runs"]),
            "error_rate_trend": trend(cur["error_rate"], prev["error_rate"]),
            "latency_trend": trend(cur["avg_latency"], prev["avg_latency"]),
            "quality_trend": trend(cur["avg_quality"], prev["avg_quality"]),
            "avg_tool_count": 0,
            "avg_llm_call_count": 0,
        }

    @staticmethod

    def _avg(values) -> float | None:
        nums = [v for v in values if v is not None]
        return round(sum(nums) / len(nums), 2) if nums else None

    @staticmethod

    def _timeline_from_payloads(payloads: list[dict]) -> list[dict]:
        buckets: dict[str, dict] = {}
        for p in payloads:
            if not p.get("start_time"):
                continue
            day = p["start_time"][:10]
            b = buckets.setdefault(day, {"date": day, "runs": 0, "errors": 0, "latencies": [], "quality_scores": []})
            b["runs"] += 1
            if p["status"] == "ERROR":
                b["errors"] += 1
            if p.get("latency") is not None:
                b["latencies"].append(p["latency"])
            if p.get("quality_score") is not None:
                b["quality_scores"].append(p["quality_score"])
        result = []
        for day in sorted(buckets):
            b = buckets[day]
            lats = b.pop("latencies")
            qs = b.pop("quality_scores")
            b["avg_latency"] = round(sum(lats) / len(lats), 2) if lats else None
            b["avg_quality"] = round(sum(qs) / len(qs), 2) if qs else None
            result.append(b)
        return result

    @staticmethod
    def _payload_user_id(payload: dict) -> str | None:
        if payload.get("user_id"):
            return payload["user_id"]
        inputs = payload.get("inputs_raw")
        return inputs.get("user_id") if isinstance(inputs, dict) else None

    @staticmethod

    def _payload_has_document(payload: dict, doc_id: int) -> bool:
        document_ids = payload.get("document_ids")
        if isinstance(document_ids, str):
            document_ids = _parse_json(document_ids)
        if not isinstance(document_ids, list):
            return False
        return str(doc_id) in {str(value) for value in document_ids}

    @staticmethod

    def _summary_payload(payload: dict) -> dict:
        return {
            "id": payload["id"],
            "name": payload["name"],
            "status": payload.get("status"),
            "start_time": payload.get("start_time"),
            "latency": payload.get("latency"),
            "error": payload.get("error"),
            "agent_name": payload.get("agent_name"),
            "tool_count": payload.get("tool_count"),
            "llm_call_count": payload.get("llm_call_count"),
            "quality_score": payload.get("quality_score"),
            "user_feedback": payload.get("user_feedback"),
            "quality_detail": payload.get("quality_detail"),
            "total_tokens": payload.get("total_tokens"),
        }


    def _sessions_from_payloads(self, payloads: list[dict]) -> list[dict]:
        sessions: dict[str, dict] = {}
        for p in payloads:
            tid = p.get("thread_id")
            if not tid:
                continue  # traces without a thread_id don't belong to any session
            s = sessions.setdefault(tid, {"thread_id": tid, "created_at": p.get("start_time"), "ended_at": p.get("end_time"), "trace_count": 0, "input_tokens": 0, "output_tokens": 0, "_quality_scores": [], "_user_ids": set()})
            s["trace_count"] += 1
            s["input_tokens"] += p.get("prompt_tokens") or 0
            s["output_tokens"] += p.get("completion_tokens") or 0
            if p.get("quality_score") is not None:
                s["_quality_scores"].append(p["quality_score"])
            uid = self._payload_user_id(p)
            if uid:
                s["_user_ids"].add(uid)
            if p.get("start_time") and (s["created_at"] is None or p["start_time"] < s["created_at"]):
                s["created_at"] = p["start_time"]
            if p.get("end_time") and (s["ended_at"] is None or p["end_time"] > s["ended_at"]):
                s["ended_at"] = p["end_time"]
        result = []
        for s in sessions.values():
            qs = s.pop("_quality_scores")
            uids = s.pop("_user_ids")
            result.append({
                **s,
                "duration_seconds": self._duration_from_iso(s["created_at"], s["ended_at"]),
                "total_tokens": s["input_tokens"] + s["output_tokens"],
                "avg_quality_score": self._avg(qs),
                "user_ids": sorted(uids),
            })
        return result


    def _session_detail_from_payloads(self, thread_id: str, payloads: list[dict]) -> dict:
        sessions = self._sessions_from_payloads(payloads)
        s = sessions[0] if sessions else {"created_at": None, "ended_at": None, "duration_seconds": None, "trace_count": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "avg_quality_score": None, "user_ids": []}
        return {"thread_id": thread_id, **{k: s[k] for k in ["created_at", "ended_at", "duration_seconds", "trace_count", "input_tokens", "output_tokens", "total_tokens", "avg_quality_score", "user_ids"]}, "traces": payloads}

    @staticmethod

    def _duration_from_iso(start: str | None, end: str | None) -> float | None:
        if not start or not end:
            return None
        try:
            return round((datetime.fromisoformat(end.replace("Z", "+00:00")) - datetime.fromisoformat(start.replace("Z", "+00:00"))).total_seconds(), 1)
        except Exception:
            return None

    @staticmethod

    def _sort_session_like(rows: list[dict], order_by: str, order_dir: str) -> None:
        reverse = order_dir.lower() != "asc"
        sort_key_fn = {
            "created_at": lambda x: x["created_at"] or "",
            "duration": lambda x: x["duration_seconds"] or 0,
            "trace_count": lambda x: x["trace_count"],
            "total_tokens": lambda x: x["total_tokens"],
            "avg_quality": lambda x: x["avg_quality_score"] or 0,
        }.get(order_by, lambda x: x["created_at"] or "")
        rows.sort(key=sort_key_fn, reverse=reverse)


    def _users_from_payloads(self, payloads: list[dict]) -> list[dict]:
        users: dict[str, dict] = {}
        for p in payloads:
            uid = self._payload_user_id(p)
            if not uid:
                continue
            u = users.setdefault(uid, {"user_id": uid, "first_event": p.get("start_time"), "last_event": p.get("start_time"), "thread_ids": set(), "trace_count": 0, "input_tokens": 0, "output_tokens": 0, "_quality_scores": []})
            u["trace_count"] += 1
            u["input_tokens"] += p.get("prompt_tokens") or 0
            u["output_tokens"] += p.get("completion_tokens") or 0
            if p.get("quality_score") is not None:
                u["_quality_scores"].append(p["quality_score"])
            if p.get("thread_id"):
                u["thread_ids"].add(p["thread_id"])
            if p.get("start_time"):
                u["first_event"] = min(u["first_event"], p["start_time"]) if u["first_event"] else p["start_time"]
                u["last_event"] = max(u["last_event"], p["start_time"]) if u["last_event"] else p["start_time"]
        result = []
        for u in users.values():
            qs = u.pop("_quality_scores")
            tids = u.pop("thread_ids")
            result.append({**u, "thread_count": len(tids), "total_tokens": u["input_tokens"] + u["output_tokens"], "avg_quality_score": self._avg(qs)})
        return result
