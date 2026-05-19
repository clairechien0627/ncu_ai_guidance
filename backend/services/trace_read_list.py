"""List-style trace read operations."""
from __future__ import annotations

from services.trace_read_common import *  # noqa: F401,F403
from services.trace_read_core import _TraceReadCore


class TraceListReadService:
    def __init__(self, core: _TraceReadCore):
        self.core = core

    def list_traces(self, *, limit: int = 40, offset: int = 0, **filters) -> list[dict]:
        payloads = self.core._merged_payloads(filters, include_display=False)
        payloads.sort(key=lambda p: p.get("start_time") or "", reverse=True)
        return payloads[offset: offset + limit]


    def document_traces(self, doc_id: int, *, limit: int = 10) -> list[dict]:
        # Use text-contains pre-filter on document_ids JSON column to avoid full table scan.
        # Exact membership is validated in Python via _payload_has_document.
        doc_str = str(doc_id)
        legacy_candidates = (
            self.core.db.query(Trace)
            .filter(
                Trace.document_ids.isnot(None),
                Trace.display.isnot(None),
                Trace.document_ids.contains(doc_str),
            )
            .order_by(Trace.start_time.desc())
            .limit(limit * 5)
            .all()
        )
        payloads = [
            legacy_trace_payload(t, include_raw=True, include_display=True)
            for t in legacy_candidates
            if self.core._payload_has_document(
                legacy_trace_payload(t, include_raw=True, include_display=False), doc_id
            )
        ]

        # Also check v2 traces (document_ids stored in metadata_json)
        v2_candidates = self.core._v2_filtered({})
        v2_ids_already = {p["id"] for p in payloads}
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
                if row.trace_id in v2_ids_already:
                    continue
                p = _build_v2_payload(row, scores_by.get(row.trace_id, []), obs_by.get(row.trace_id, []),
                                      include_raw=True, include_display=True)
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
                "display": p.get("display"),
                "task_type": p.get("task_type"),
                "route_intent": p.get("route_intent"),
                "agent_name": p.get("agent_name"),
                "prompt_name": p.get("prompt_name"),
                "prompt_version": p.get("prompt_version"),
                "base_prompt_name": p.get("base_prompt_name"),
                "task_prompt_name": p.get("task_prompt_name"),
                "quality_prompt_name": p.get("quality_prompt_name"),
                "base_prompt_hash": p.get("base_prompt_hash"),
                "task_prompt_hash": p.get("task_prompt_hash"),
                "quality_prompt_hash": p.get("quality_prompt_hash"),
                "prompt_stack_name": p.get("prompt_stack_name"),
                "prompt_stack_json": p.get("prompt_stack_json"),
                "primary_prompt_json": p.get("primary_prompt_json"),
                "workflow_prompts_json": p.get("workflow_prompts_json"),
                "prompt_stack_tokens": p.get("prompt_stack_tokens"),
                "tool_count": p.get("tool_count"),
                "llm_call_count": p.get("llm_call_count"),
                "quality_score": p.get("quality_score"),
                "user_feedback": p.get("user_feedback"),
                "original_intent": p.get("original_intent"),
                "resolved_intent": p.get("resolved_intent"),
                "quality_detail": p.get("quality_detail"),
                "runtime_prompt_metadata": p.get("runtime_prompt_metadata"),
            }
            for p in combined[:limit]
        ]


    def errors(self, *, limit: int = 40) -> list[dict]:
        payloads = self.core._merged_payloads({"status": "error"}, include_display=False)
        payloads.sort(key=lambda p: p.get("start_time") or "", reverse=True)
        return [self.core._summary_payload(p) for p in payloads[:limit]]


    def slow_runs(self, *, limit: int = 40, min_latency: float = 10) -> list[dict]:
        payloads = self.core._merged_payloads({"min_latency": min_latency}, include_display=False)
        payloads.sort(key=lambda p: p.get("latency") or 0, reverse=True)
        return [self.core._summary_payload(p) for p in payloads[:limit]]


    def compare_prompt_versions(self, v1: str, v2: str, *, limit: int = 200) -> dict:
        def stats(version: str) -> dict:
            payloads = self.core._merged_payloads({"prompt_version": version}, include_display=False, legacy_limit=limit)
            payloads.sort(key=lambda p: p.get("start_time") or "", reverse=True)
            payloads = payloads[:limit]
            if not payloads:
                return {"version": version, "runs": 0, "errors": 0, "avg_latency": None, "avg_quality": None, "error_rate": None}
            latencies = [p["latency"] for p in payloads if p.get("latency") is not None]
            quality = [p["quality_score"] for p in payloads if p.get("quality_score") is not None]
            errors = sum(1 for p in payloads if p.get("status") == "error")
            return {
                "version": version,
                "runs": len(payloads),
                "errors": errors,
                "error_rate": round(errors / len(payloads), 3),
                "avg_latency": round(sum(latencies) / len(latencies), 2) if latencies else None,
                "avg_quality": round(sum(quality) / len(quality), 2) if quality else None,
            }

        return {"v1": stats(v1), "v2": stats(v2)}

