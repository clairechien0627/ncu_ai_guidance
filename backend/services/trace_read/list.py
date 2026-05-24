"""List-style trace read operations."""
from __future__ import annotations

from .common import *  # noqa: F401,F403
from .core import _TraceReadCore


class TraceListReadService:
    def __init__(self, core: _TraceReadCore):
        self.core = core

    def list_traces(self, *, limit: int = 40, offset: int = 0, **filters) -> list[dict]:
        payloads = self.core._merged_payloads(filters)
        payloads.sort(key=lambda p: p.get("start_time") or "", reverse=True)
        return payloads[offset: offset + limit]


    def document_traces(self, doc_id: int, *, limit: int = 10) -> list[dict]:
        payloads: list[dict] = []
        v2_candidates = self.core._v2_filtered({})
        if v2_candidates:
            trace_ids = [r.trace_id for r in v2_candidates]
            all_scores = self.core.db.query(Score).filter(Score.trace_id.in_(trace_ids)).all()
            all_obs = self.core.db.query(Observation).filter(Observation.trace_id.in_(trace_ids)).all()
            scores_by = {s.trace_id: [] for s in all_scores}
            for s in all_scores:
                if s.trace_id:
                    scores_by.setdefault(s.trace_id, []).append(s)
            obs_by = {}
            for o in all_obs:
                obs_by.setdefault(o.trace_id, []).append(o)
            for row in v2_candidates:
                p = _build_v2_payload(row, scores_by.get(row.trace_id, []), obs_by.get(row.trace_id, []),
                                      include_raw=True)
                if self.core._payload_has_document(p, doc_id):
                    payloads.append(p)

        payloads.sort(key=lambda p: p.get("start_time") or "", reverse=True)

        extract_traces = [
            p for p in payloads
            if p.get("thread_id") and str(p["thread_id"]).startswith("_extract_")
        ]
        other_traces = [
            p for p in payloads
            if p.get("thread_id") and not str(p["thread_id"]).lstrip("-").isdigit()
        ]
        seen = {p["id"] for p in extract_traces}
        combined = extract_traces + [p for p in other_traces if p["id"] not in seen]

        return [
            {
                "id": p["id"],
                "name": p["name"],
                "status": p["status"],
                "start_time": p["start_time"],
                "latency": p["latency"],
                "error": p["error"],
                "url": p.get("url"),
                "input": p.get("input"),
                "output": p.get("output"),
                "agent_name": p.get("agent_name"),
                "tool_count": p.get("tool_count"),
                "llm_call_count": p.get("llm_call_count"),
                "quality_score": p.get("quality_score"),
                "user_feedback": p.get("user_feedback"),
                "quality_detail": p.get("quality_detail"),
            }
            for p in combined[:limit]
        ]


    def errors(self, *, limit: int = 40) -> list[dict]:
        payloads = self.core._merged_payloads({"status": "ERROR"})
        payloads.sort(key=lambda p: p.get("start_time") or "", reverse=True)
        return [self.core._summary_payload(p) for p in payloads[:limit]]


    def slow_runs(self, *, limit: int = 40, min_latency: float = 10) -> list[dict]:
        payloads = self.core._merged_payloads({"min_latency": min_latency})
        payloads.sort(key=lambda p: p.get("latency") or 0, reverse=True)
        return [self.core._summary_payload(p) for p in payloads[:limit]]


    def compare_prompt_versions(self, v1: str, v2: str, *, limit: int = 200) -> dict:
        def stats(version: str) -> dict:
            payloads = self.core._merged_payloads({"prompt_version": version})
            payloads.sort(key=lambda p: p.get("start_time") or "", reverse=True)
            payloads = payloads[:limit]
            if not payloads:
                return {"version": version, "runs": 0, "errors": 0, "avg_latency": None, "avg_quality": None, "error_rate": None}
            latencies = [p["latency"] for p in payloads if p.get("latency") is not None]
            quality = [p["quality_score"] for p in payloads if p.get("quality_score") is not None]
            errors = sum(1 for p in payloads if p.get("status") == "ERROR")
            return {
                "version": version,
                "runs": len(payloads),
                "errors": errors,
                "error_rate": round(errors / len(payloads), 3),
                "avg_latency": round(sum(latencies) / len(latencies), 2) if latencies else None,
                "avg_quality": round(sum(quality) / len(quality), 2) if quality else None,
            }

        return {"v1": stats(v1), "v2": stats(v2)}

