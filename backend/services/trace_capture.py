"""LocalTracer — stores LangChain/LangGraph run events in local PostgreSQL.

Drop-in replacement for LangSmith cloud tracing during development.
One instance per agent call; flushed to DB when the root chain ends.
"""
import asyncio
import json
import logging
import os
from datetime import datetime, timezone

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult

logger = logging.getLogger(__name__)


def _get_default_environment() -> str:
    return os.environ.get("LANGFUSE_TRACING_ENVIRONMENT", "default")


# ── Serialisation helpers ──────────────────────────────────────────────────────

def _json_default(obj):
    if hasattr(obj, "model_dump"):
        try:
            return obj.model_dump()
        except Exception:
            pass
    if hasattr(obj, "dict"):
        try:
            return obj.dict()
        except Exception:
            pass
    return repr(obj)[:300]


def _safe_json(obj) -> str | None:
    if obj is None:
        return None
    try:
        return json.dumps(obj, ensure_ascii=False, default=_json_default)
    except Exception:
        return json.dumps(repr(obj)[:500])


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ── Tracer ─────────────────────────────────────────────────────────────────────

class LocalTracer(BaseCallbackHandler):
    """Collects LangChain/LangGraph run events and writes them to local PostgreSQL.

    Usage:
        tracer = LocalTracer(thread_id=thread_id, document_ids=document_ids)
        config = {"configurable": {...}, "callbacks": [tracer]}
        await agent.ainvoke(..., config)
    """

    def __init__(
        self,
        thread_id: str,
        document_ids: list[int] | None = None,
        agent_name: str | None = None,
        prompt_name: str | None = None,
        prompt_version: str | None = None,
        quality_score: float | None = None,
        user_feedback: str | None = None,
        trace_id: str | None = None,
        parent_observation_id: str | None = None,
        environment: str | None = None,
        user_id: str | None = None,
    ):
        super().__init__()
        self.thread_id = thread_id
        self.document_ids = document_ids
        self._events: dict[str, dict] = {}   # run_id (str) → event data
        self.agent_name = agent_name
        self.prompt_name = prompt_name
        self.prompt_version = prompt_version
        self.quality_score = quality_score
        self.user_feedback = user_feedback
        self.trace_id = trace_id
        self.parent_observation_id = parent_observation_id
        self.environment = environment or _get_default_environment()
        self.user_id = user_id
        self._root_observation_id: str | None = None

    def _apply_metadata(self, metadata: dict | None) -> None:
        if not isinstance(metadata, dict):
            return
        self.agent_name = self.agent_name or metadata.get("agent_name")
        self.prompt_name = self.prompt_name or metadata.get("prompt_name")
        self.prompt_version = self.prompt_version or metadata.get("prompt_version")
        self.quality_score = self.quality_score if self.quality_score is not None else metadata.get("quality_score")
        self.user_feedback = self.user_feedback or metadata.get("user_feedback")

    def _ev(self, run_id) -> dict:
        rid = str(run_id)
        if rid not in self._events:
            self._events[rid] = {}
        return self._events[rid]

    # ── Chain ──────────────────────────────────────────────────────────────────

    def on_chain_start(self, serialized, inputs, *, run_id, parent_run_id=None, **kwargs):
        self._apply_metadata(kwargs.get("metadata"))
        rid = str(run_id)
        parent_obs_id = str(parent_run_id) if parent_run_id else None
        if parent_obs_id is None and self._root_observation_id is None:
            self._root_observation_id = rid
        s = serialized or {}
        # Prefer the run name from kwargs (LangGraph passes node name here),
        # then fall back to serialized fields.
        name = kwargs.get("name") or s.get("name") or (s.get("id") or ["chain"])[-1]
        ev = self._ev(rid)
        ev.update({
            "run_id": rid,
            "parent_observation_id": parent_obs_id,
            "type": "CHAIN",
            "name": name,
            "inputs": _safe_json(inputs),
            "start_time": _utcnow(),
        })

    def on_chain_end(self, outputs, *, run_id, **kwargs):
        rid = str(run_id)
        ev = self._ev(rid)
        ev.update({"outputs": _safe_json(outputs), "end_time": _utcnow()})
        if rid == self._root_observation_id:
            self._flush(outputs)

    def on_chain_error(self, error, *, run_id, **kwargs):
        # Client disconnect causes a CancelledError propagated through anyio cancel
        # scopes. This is not an agent error — skip recording it to avoid false
        # positive red traces in the monitor.
        err_str = str(error)
        if "Cancelled via cancel scope" in err_str or "CancelledError" in type(error).__name__:
            return
        rid = str(run_id)
        ev = self._ev(rid)
        ev.update({"end_time": _utcnow(), "error": err_str})
        if rid == self._root_observation_id:
            self._flush(None)

    # ── LLM ────────────────────────────────────────────────────────────────────

    def on_chat_model_start(self, serialized, messages, *, run_id, parent_run_id=None, **kwargs):
        rid = str(run_id)
        msg_data = []
        for batch in messages:
            for msg in batch:
                msg_data.append({
                    "type": getattr(msg, "type", type(msg).__name__),
                    "content": str(msg.content),
                })
        ev = self._ev(rid)
        ev.update({
            "run_id": rid,
            "parent_observation_id": str(parent_run_id) if parent_run_id else None,
            "type": "GENERATION",
            "name": (serialized or {}).get("name", "LLM"),
            "inputs": json.dumps({"messages": msg_data}, ensure_ascii=False),
            "start_time": _utcnow(),
        })

    def on_llm_end(self, response: LLMResult, *, run_id, **kwargs):
        rid = str(run_id)
        ev = self._ev(rid)
        usage = (response.llm_output or {}).get("token_usage", {})
        content = ""
        if response.generations:
            gen = response.generations[0][0] if response.generations[0] else None
            if gen:
                content = getattr(gen, "text", None) or str(gen)
        ev.update({
            "outputs": json.dumps({"content": content}, ensure_ascii=False),
            "end_time": _utcnow(),
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
        })

    def on_llm_error(self, error, *, run_id, **kwargs):
        self._ev(str(run_id)).update({"end_time": _utcnow(), "error": str(error)})

    # ── Tool ───────────────────────────────────────────────────────────────────

    def on_tool_start(self, serialized, input_str, *, run_id, parent_run_id=None, **kwargs):
        rid = str(run_id)
        ev = self._ev(rid)
        ev.update({
            "run_id": rid,
            "parent_observation_id": str(parent_run_id) if parent_run_id else None,
            "type": "TOOL",
            "name": (serialized or {}).get("name", "tool"),
            "inputs": json.dumps({"input": str(input_str)}, ensure_ascii=False),
            "start_time": _utcnow(),
        })

    def on_tool_end(self, output, *, run_id, **kwargs):
        rid = str(run_id)
        ev = self._ev(rid)
        ev.update({
            "outputs": json.dumps({"output": str(output)}, ensure_ascii=False),
            "end_time": _utcnow(),
        })

    def on_tool_error(self, error, *, run_id, **kwargs):
        self._ev(str(run_id)).update({"end_time": _utcnow(), "error": str(error)})

    # ── Flush ──────────────────────────────────────────────────────────────────

    def _flush(self, root_outputs):
        """Serialize event data in memory, then schedule DB/outbox writes.

        The serialisation step is synchronous and fast (pure memory reads).
        The actual DB I/O runs in a thread pool via asyncio.to_thread so it
        never blocks the event loop.  Falls back to synchronous writes when
        there is no running event loop (e.g. test context).
        """
        rows = self._build_trace_rows(root_outputs)
        if not rows:
            return
        events = self._build_trace_events(rows)
        try:
            loop = asyncio.get_running_loop()
            if events:
                from services.trace_ingestion import TraceEventIngestor
                loop.create_task(TraceEventIngestor.enqueue(events))
        except RuntimeError:
            if events:
                from services.trace_ingestion import TraceEventIngestor, TraceIngestionWorker
                TraceEventIngestor.enqueue_sync(events)
                TraceIngestionWorker.process_pending()

    def _build_trace_rows(self, root_outputs) -> list[dict]:
        """Build trace dicts from in-memory event data without any I/O."""
        doc_ids_json = json.dumps(self.document_ids) if self.document_ids else None

        total_prompt_tokens = sum(ev.get("prompt_tokens") or 0 for ev in self._events.values())
        total_completion_tokens = sum(ev.get("completion_tokens") or 0 for ev in self._events.values())

        # Run IDs that are referenced as a parent by at least one other run.
        # Used to skip LangGraph's internal state-passing nodes (e.g. __start__,
        # __end__) which fire on_chain_start/end with no real work and no children.
        parent_ids = {
            ev["parent_observation_id"]
            for ev in self._events.values()
            if ev.get("parent_observation_id")
        }

        rows = []
        for ev in self._events.values():
            if "run_id" not in ev or "type" not in ev:
                continue
            is_root = ev["run_id"] == self._root_observation_id
            # Skip leaf chains with the default name "chain" — these are LangGraph
            # internal graph-traversal nodes that contain no LLM/tool work.
            if (not is_root
                    and ev.get("type") == "CHAIN"
                    and ev.get("name") == "chain"
                    and ev["run_id"] not in parent_ids):
                continue
            parent_id = ev.get("parent_observation_id")
            rows.append({
                "run_id": ev["run_id"],
                "parent_observation_id": parent_id,
                "type": ev["type"],
                "name": ev.get("name", "unknown"),
                "inputs": ev.get("inputs"),
                "outputs": ev.get("outputs"),
                "error": ev.get("error"),
                "start_time": ev.get("start_time", _utcnow()),
                "end_time": ev.get("end_time"),
                "prompt_tokens": total_prompt_tokens if is_root else ev.get("prompt_tokens"),
                "completion_tokens": total_completion_tokens if is_root else ev.get("completion_tokens"),
                "thread_id": self.thread_id,
                "document_ids": doc_ids_json,
                "event_metadata": ev.get("metadata") or {},
                "agent_name": self.agent_name,
                "prompt_name": self.prompt_name,
                "prompt_version": self.prompt_version,
                "quality_score": self.quality_score if is_root else None,
                "user_feedback": self.user_feedback if is_root else None,
                "environment": self.environment,
                "user_id": self.user_id if is_root else None,
            })
        return rows

    def _build_trace_events(self, rows: list[dict]) -> list[dict]:
        """Build normalized Trace System v2 events from collected callback rows."""
        root_row = next((row for row in rows if row["run_id"] == self._root_observation_id), None)
        if root_row is None:
            return []

        def _loads(value):
            if not value:
                return None
            if isinstance(value, str):
                try:
                    return json.loads(value)
                except Exception:
                    return value
            return value

        root_metadata = {
            "agent_name": self.agent_name,
            "document_ids": self.document_ids,
        }
        root_metadata = {k: v for k, v in root_metadata.items() if v is not None}

        _VALID_STATUSES = {"DEBUG", "DEFAULT", "WARNING", "ERROR"}

        def _resolve_status(row: dict) -> str:
            if row.get("error"):
                return "ERROR"
            meta_status = str(row.get("event_metadata", {}).get("status") or "").upper()
            return meta_status if meta_status in _VALID_STATUSES else "DEFAULT"

        def _obs_body(row: dict, trace_id: str, parent_observation_id) -> dict:
            usage: dict = {}
            if row.get("prompt_tokens") is not None:
                usage["input"] = row["prompt_tokens"]
            if row.get("completion_tokens") is not None:
                usage["output"] = row["completion_tokens"]
            if usage:
                usage["total"] = (usage.get("input") or 0) + (usage.get("output") or 0)
                usage["unit"] = "TOKENS"
            return {
                "observation_id": row["run_id"],
                "trace_id": trace_id,
                "parent_observation_id": parent_observation_id,
                "type": row.get("type") or "SPAN",
                "name": row.get("name") or "observation",
                "usage": usage or None,
                "prompt_name": row.get("prompt_name"),
                "prompt_version": row.get("prompt_version"),
                "input": _loads(row.get("inputs")),
                "output": _loads(row.get("outputs")),
                "metadata": row.get("event_metadata") or None,
                "status": _resolve_status(row),
                "status_message": row.get("error"),
                "start_time": row.get("start_time"),
                "end_time": row.get("end_time"),
            }

        if self.trace_id:
            # Sub-agent tracer: all runs become observations under the parent trace.
            events = []
            for row in rows:
                is_root = row["run_id"] == self._root_observation_id
                # Root run → parent is the agent SPAN (parent_observation_id), otherwise top-level
                # Children → point to their actual LangChain parent (also an observation)
                parent_obs = (self.parent_observation_id if is_root else row.get("parent_observation_id"))
                events.append({
                    "event_id": f"observation-create:{row['run_id']}",
                    "event_type": "observation-create",
                    "body": _obs_body(row, self.trace_id, parent_obs),
                })
            return events

        # Root tracer: create one trace + observations for every non-root run
        events = [{
            "event_id": f"trace-create:{root_row['run_id']}",
            "event_type": "trace-create",
            "body": {
                "trace_id": root_row["run_id"],
                "name": root_row.get("name") or "trace",
                "thread_id": self.thread_id,
                "user_id": self.user_id,
                "environment": self.environment,
                "input": _loads(root_row.get("inputs")),
                "output": _loads(root_row.get("outputs")),
                "metadata": root_metadata,
                "tags": [self.agent_name] if self.agent_name else [],
                "start_time": root_row.get("start_time"),
                "end_time": root_row.get("end_time"),
            },
        }]
        for row in rows:
            if row["run_id"] == self._root_observation_id:
                continue
            # Direct children of root → parent_observation_id=None (root-level observations)
            # Deeper children → point to their direct LangChain parent
            parent_obs = None if row.get("parent_observation_id") == self._root_observation_id else row.get("parent_observation_id")
            events.append({
                "event_id": f"observation-create:{row['run_id']}",
                "event_type": "observation-create",
                "body": _obs_body(row, root_row["run_id"], parent_obs),
            })
        return events
