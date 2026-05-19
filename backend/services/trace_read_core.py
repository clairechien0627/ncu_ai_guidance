"""Internal core helpers for TraceReadService facade.

This module intentionally keeps the compatibility-heavy private helpers in one
place while public read capabilities live in smaller service classes.
"""
from __future__ import annotations

from services.trace_read_common import *  # noqa: F401,F403


class _TraceReadCore:
    def __init__(self, db: Session):
        self.db = db


    def has_v2(self) -> bool:
        return bool(self.db.query(TraceV2.id).first())


    def environments(self) -> list[str]:
        values = {row[0] for row in self.db.query(TraceV2.environment).distinct().all() if row[0]}
        values.update({row[0] for row in self.db.query(Trace.environment).filter(_agent_execution_root(), Trace.environment.isnot(None)).distinct().all() if row[0]})
        return sorted(values)


    def _merged_payloads(
        self,
        filters: dict,
        *,
        include_raw: bool = False,
        include_display: bool = True,
        legacy_limit: int = 100000,
    ) -> list[dict]:
        payloads: list[dict] = []
        v2_ids: set[str] = set()
        if self.has_v2():
            v2_rows = self._v2_filtered(filters)
            if v2_rows:
                trace_ids = [r.trace_id for r in v2_rows]
                v2_ids = set(trace_ids)

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

                cleaned = {k: _clean(v) if isinstance(v, str) else v for k, v in filters.items()}
                for row in v2_rows:
                    payload = _build_v2_payload(
                        row,
                        scores_by_trace.get(row.trace_id, []),
                        obs_by_trace.get(row.trace_id, []),
                        include_raw=include_raw,
                        include_display=include_display,
                    )
                    if filters.get("level"):
                        lvl_list = [l.strip() for l in str(filters["level"]).split(",") if l.strip()]
                        if lvl_list and (payload.get("level") or "DEFAULT") not in lvl_list:
                            continue
                    if cleaned.get("min_latency") is not None and (payload["latency"] or 0) < float(cleaned["min_latency"]):
                        continue
                    if cleaned.get("max_quality") is not None:
                        qs = payload["quality_score"]
                        if qs is None or qs >= float(cleaned["max_quality"]):
                            continue
                    if cleaned.get("min_quality") is not None:
                        qs = payload["quality_score"]
                        if qs is None or qs < float(cleaned["min_quality"]):
                            continue
                    if cleaned.get("has_score") is True and payload["quality_score"] is None:
                        continue
                    if cleaned.get("has_score") is False and payload["quality_score"] is not None:
                        continue
                    if cleaned.get("level"):
                        if payload.get("level") != cleaned["level"]:
                            continue
                    total_tok = (payload.get("prompt_tokens") or 0) + (payload.get("completion_tokens") or 0)
                    if cleaned.get("min_tokens") is not None and total_tok < float(cleaned["min_tokens"]):
                        continue
                    if cleaned.get("max_tokens") is not None and total_tok > float(cleaned["max_tokens"]):
                        continue
                    if cleaned.get("min_input_tokens") is not None and (payload.get("prompt_tokens") or 0) < float(cleaned["min_input_tokens"]):
                        continue
                    if cleaned.get("min_output_tokens") is not None and (payload.get("completion_tokens") or 0) < float(cleaned["min_output_tokens"]):
                        continue
                    payloads.append(payload)

        legacy_rows = self._legacy_filtered(filters, limit=legacy_limit, offset=0)
        payloads.extend(
            legacy_trace_payload(row, include_raw=include_raw, include_display=include_display)
            for row in legacy_rows
            # When v2 data exists, skip legacy child traces (task/chain runs under a router);
            # they belong as observations, not top-level trace rows.
            if row.run_id not in v2_ids and (not v2_ids or row.parent_run_id is None)
        )
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
        rows = q.order_by(TraceV2.start_time.desc()).all()
        if not rows:
            return []
        cleaned = {k: _clean(v) if isinstance(v, str) else v for k, v in filters.items()}
        meta_keys = {"task_type", "route_intent", "prompt_name", "prompt_version", "original_intent", "resolved_intent"}
        active_meta = {k: cleaned[k] for k in meta_keys if cleaned.get(k)}
        if not active_meta:
            return rows
        result = []
        for trace in rows:
            meta = _metadata(trace)
            if any((meta.get(k) or "unknown") != v for k, v in active_meta.items()):
                continue
            result.append(trace)
        return result


    def _legacy_filtered(self, filters: dict, *, limit: int, offset: int) -> list[Trace]:
        q = self.db.query(Trace).filter(_agent_execution_root())
        cleaned = {k: _clean(v) if isinstance(v, str) else v for k, v in filters.items()}
        mapping = {
            "task_type": Trace.task_type,
            "route_intent": Trace.route_intent,
            "prompt_name": Trace.prompt_name,
            "prompt_version": Trace.prompt_version,
            "original_intent": Trace.original_intent,
            "resolved_intent": Trace.resolved_intent,
            "agent_name": Trace.agent_name,
            "environment": Trace.environment,
            "user_id": Trace.user_id,
            "name": Trace.name,
        }
        for key, col in mapping.items():
            if cleaned.get(key):
                q = q.filter(col == cleaned[key])
        if filters.get("names"):
            name_list = [n.strip() for n in str(filters["names"]).split(",") if n.strip()]
            if name_list:
                q = q.filter(Trace.name.in_(name_list))
        if filters.get("user_ids"):
            uid_list = [u.strip() for u in str(filters["user_ids"]).split(",") if u.strip()]
            if uid_list:
                q = q.filter(Trace.user_id.in_(uid_list))
        if filters.get("level"):
            lvl_list = [l.strip() for l in str(filters["level"]).split(",") if l.strip()]
            has_error = "ERROR" in lvl_list
            has_ok = bool(set(lvl_list) - {"ERROR"})
            if has_error and not has_ok:
                q = q.filter(Trace.error.isnot(None))
            elif has_ok and not has_error:
                q = q.filter(Trace.error.is_(None))
        if cleaned.get("max_quality") is not None:
            q = q.filter(Trace.quality_score.isnot(None), Trace.quality_score < cleaned["max_quality"])
        if cleaned.get("min_quality") is not None:
            q = q.filter(Trace.quality_score.isnot(None), Trace.quality_score >= cleaned["min_quality"])
        if cleaned.get("min_tokens") is not None:
            min_tok = float(cleaned["min_tokens"])
            q = q.filter((Trace.prompt_tokens + Trace.completion_tokens) >= min_tok)
        if cleaned.get("max_tokens") is not None:
            max_tok = float(cleaned["max_tokens"])
            q = q.filter((Trace.prompt_tokens + Trace.completion_tokens) <= max_tok)
        if cleaned.get("min_input_tokens") is not None:
            q = q.filter(Trace.prompt_tokens >= float(cleaned["min_input_tokens"]))
        if cleaned.get("min_output_tokens") is not None:
            q = q.filter(Trace.completion_tokens >= float(cleaned["min_output_tokens"]))
        if cleaned.get("has_score") is True:
            q = q.filter(Trace.quality_score.isnot(None))
        elif cleaned.get("has_score") is False:
            q = q.filter(Trace.quality_score.is_(None))
        if cleaned.get("date_from"):
            try:
                q = q.filter(Trace.start_time >= datetime.fromisoformat(cleaned["date_from"]))
            except ValueError:
                pass
        if cleaned.get("date_to"):
            try:
                q = q.filter(Trace.start_time <= datetime.fromisoformat(cleaned["date_to"]))
            except ValueError:
                pass
        rows = q.order_by(Trace.start_time.desc()).offset(offset).limit(limit).all()
        if cleaned.get("min_latency") is not None:
            rows = [t for t in rows if (_legacy_latency(t) or 0) >= float(cleaned["min_latency"])]
        return rows

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

    def _legacy_agg(traces: list[Trace]) -> dict:
        lats = [v for v in (_legacy_latency(t) for t in traces) if v is not None]
        qs = [t.quality_score for t in traces if t.quality_score is not None]
        errs = sum(1 for t in traces if t.error)
        return {
            "runs": len(traces),
            "errors": errs,
            "error_rate": round(errs / len(traces) * 100, 1) if traces else 0.0,
            "avg_latency": round(sum(lats) / len(lats), 2) if lats else None,
            "avg_quality": round(sum(qs) / len(qs), 2) if qs else None,
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
            if p["status"] == "error":
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
            "start_time": payload.get("start_time"),
            "latency": payload.get("latency"),
            "error": payload.get("error"),
            "task_type": payload.get("task_type"),
            "route_intent": payload.get("route_intent"),
            "agent_name": payload.get("agent_name"),
            "prompt_name": payload.get("prompt_name"),
            "prompt_version": payload.get("prompt_version"),
            "base_prompt_name": payload.get("base_prompt_name"),
            "task_prompt_name": payload.get("task_prompt_name"),
            "quality_prompt_name": payload.get("quality_prompt_name"),
            "base_prompt_hash": payload.get("base_prompt_hash"),
            "task_prompt_hash": payload.get("task_prompt_hash"),
            "quality_prompt_hash": payload.get("quality_prompt_hash"),
            "prompt_stack_name": payload.get("prompt_stack_name"),
            "prompt_stack_json": payload.get("prompt_stack_json"),
            "primary_prompt_json": payload.get("primary_prompt_json"),
            "workflow_prompts_json": payload.get("workflow_prompts_json"),
            "prompt_stack_tokens": payload.get("prompt_stack_tokens"),
            "tool_count": payload.get("tool_count"),
            "llm_call_count": payload.get("llm_call_count"),
            "quality_score": payload.get("quality_score"),
            "user_feedback": payload.get("user_feedback"),
            "original_intent": payload.get("original_intent"),
            "resolved_intent": payload.get("resolved_intent"),
            "quality_detail": payload.get("quality_detail"),
        }


    def _sessions_from_payloads(self, payloads: list[dict]) -> list[dict]:
        sessions: dict[str, dict] = {}
        for p in payloads:
            tid = p.get("thread_id")
            if not tid:
                continue  # traces without a thread_id don't belong to any session
            s = sessions.setdefault(tid, {"thread_id": tid, "_route_intents": [], "created_at": p.get("start_time"), "ended_at": p.get("end_time"), "trace_count": 0, "input_tokens": 0, "output_tokens": 0, "_quality_scores": [], "_user_ids": set()})
            s["trace_count"] += 1
            s["input_tokens"] += p.get("prompt_tokens") or 0
            s["output_tokens"] += p.get("completion_tokens") or 0
            if p.get("route_intent"):
                s["_route_intents"].append(p["route_intent"])
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
            intents = s.pop("_route_intents")
            qs = s.pop("_quality_scores")
            uids = s.pop("_user_ids")
            result.append({
                **s,
                "task_type": Counter(intents).most_common(1)[0][0] if intents else None,
                "duration_seconds": self._duration_from_iso(s["created_at"], s["ended_at"]),
                "total_tokens": s["input_tokens"] + s["output_tokens"],
                "avg_quality_score": self._avg(qs),
                "user_ids": sorted(uids),
            })
        return result


    def _session_detail_from_payloads(self, thread_id: str, payloads: list[dict]) -> dict:
        sessions = self._sessions_from_payloads(payloads)
        s = sessions[0] if sessions else {"task_type": None, "created_at": None, "ended_at": None, "duration_seconds": None, "trace_count": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "avg_quality_score": None, "user_ids": []}
        return {"thread_id": thread_id, **{k: s[k] for k in ["task_type", "created_at", "ended_at", "duration_seconds", "trace_count", "input_tokens", "output_tokens", "total_tokens", "avg_quality_score", "user_ids"]}, "traces": payloads}

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
            u = users.setdefault(uid, {"user_id": uid, "first_event": p.get("start_time"), "last_event": p.get("start_time"), "session_ids": set(), "trace_count": 0, "input_tokens": 0, "output_tokens": 0, "_quality_scores": []})
            u["trace_count"] += 1
            u["input_tokens"] += p.get("prompt_tokens") or 0
            u["output_tokens"] += p.get("completion_tokens") or 0
            if p.get("quality_score") is not None:
                u["_quality_scores"].append(p["quality_score"])
            if p.get("thread_id"):
                u["session_ids"].add(p["thread_id"])
            if p.get("start_time"):
                u["first_event"] = min(u["first_event"], p["start_time"]) if u["first_event"] else p["start_time"]
                u["last_event"] = max(u["last_event"], p["start_time"]) if u["last_event"] else p["start_time"]
        result = []
        for u in users.values():
            qs = u.pop("_quality_scores")
            sids = u.pop("session_ids")
            result.append({**u, "session_count": len(sids), "total_tokens": u["input_tokens"] + u["output_tokens"], "avg_quality_score": self._avg(qs)})
        return result

    @staticmethod

    def _legacy_dimension_avgs(scored: list[Trace]) -> dict:
        sums: dict[str, list[float]] = {k: [] for k in QUALITY_DIMENSIONS}
        for trace in scored:
            detail = _legacy_parse_quality(trace.quality_detail)
            if not isinstance(detail, dict):
                continue
            for key in sums:
                value = detail.get(key)
                if isinstance(value, (int, float)):
                    sums[key].append(float(value))
        return {k: round(sum(v) / len(v), 2) if v else None for k, v in sums.items()}
