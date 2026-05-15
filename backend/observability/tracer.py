"""LocalTracer — stores LangChain/LangGraph run events in local PostgreSQL.

Drop-in replacement for LangSmith cloud tracing during development.
One instance per agent call; flushed to DB when the root chain ends.
"""
import asyncio
import json
import logging
from datetime import datetime, timezone

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult

logger = logging.getLogger(__name__)


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


# ── Display builder ────────────────────────────────────────────────────────────

def _build_display(outputs: dict) -> dict:
    """Reconstruct a TraceDisplay-compatible dict from LangGraph root chain outputs."""
    display: dict = {"messages": [], "answer": None, "sources": []}
    if not outputs:
        return display

    # Structured response (answer + sources)
    sr = outputs.get("structured_response")
    if isinstance(sr, dict):
        display["answer"] = sr.get("answer")
        display["sources"] = sr.get("sources", [])
    elif hasattr(sr, "answer"):
        display["answer"] = sr.answer
        display["sources"] = list(getattr(sr, "sources", []))

    # Message sequence — each message is parsed individually; a format change in
    # one message only skips that message, it never aborts the whole tracer write.
    for msg in outputs.get("messages", []):
        try:
            if not isinstance(msg, dict):
                if hasattr(msg, "model_dump"):
                    msg = msg.model_dump()
                else:
                    continue

            msg_type = msg.get("type", "")

            if msg_type == "human":
                content = msg.get("content", "")
                if isinstance(content, list):
                    content = " ".join(
                        c.get("text", "") for c in content if isinstance(c, dict)
                    )
                display["messages"].append({"role": "human", "content": content})

            elif msg_type == "ai":
                tool_calls_lc = msg.get("tool_calls", [])
                tool_calls_ak = (msg.get("additional_kwargs") or {}).get("tool_calls", [])
                content = msg.get("content", "")

                if tool_calls_lc:
                    parsed = []
                    for tc in tool_calls_lc:
                        args = tc.get("args", {})
                        if isinstance(args, str):
                            try:
                                args = json.loads(args)
                            except Exception:
                                args = {}
                        parsed.append({
                            "tool": tc.get("name", ""),
                            "call_id": tc.get("id", ""),
                            "args": args,
                        })
                    display["messages"].append({"role": "ai_tool_call", "tool_calls": parsed})
                elif tool_calls_ak:
                    parsed = []
                    for tc in tool_calls_ak:
                        func = tc.get("function", {})
                        try:
                            args = json.loads(func.get("arguments", "{}"))
                        except Exception:
                            args = {}
                        parsed.append({
                            "tool": func.get("name", tc.get("name", "")),
                            "call_id": tc.get("id", ""),
                            "args": args,
                        })
                    display["messages"].append({"role": "ai_tool_call", "tool_calls": parsed})
                elif content and not display["answer"]:
                    try:
                        parsed_content = json.loads(content)
                        if isinstance(parsed_content, dict) and "answer" in parsed_content:
                            if not display["answer"]:
                                display["answer"] = parsed_content["answer"]
                            if not display["sources"]:
                                display["sources"] = parsed_content.get("sources", [])
                        else:
                            display["messages"].append({"role": "ai", "content": content})
                    except Exception:
                        display["messages"].append({"role": "ai", "content": content})

            elif msg_type == "tool":
                content_raw = str(msg.get("content", ""))
                chunks = []
                try:
                    parsed = json.loads(content_raw)
                    if isinstance(parsed, dict):
                        for r in parsed.get("results", []):
                            if isinstance(r, dict):
                                chunks.append({
                                    "filename": r.get("filename", ""),
                                    "page": r.get("page"),
                                    "content": r.get("content", ""),
                                })
                except Exception:
                    pass
                display["messages"].append({
                    "role": "tool",
                    "tool": msg.get("name", ""),
                    "call_id": msg.get("tool_call_id", ""),
                    "chunks": chunks,
                    "raw": content_raw,
                })
        except Exception as _msg_exc:
            logger.debug("_build_display: skipping unparseable message: %s", _msg_exc)

    return display


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
        task_type: str | None = None,
        route_intent: str | None = None,
        agent_name: str | None = None,
        prompt_name: str | None = None,
        prompt_version: str | None = None,
        base_prompt_name: str | None = None,
        task_prompt_name: str | None = None,
        quality_prompt_name: str | None = None,
        base_prompt_hash: str | None = None,
        task_prompt_hash: str | None = None,
        quality_prompt_hash: str | None = None,
        prompt_stack_name: str | None = None,
        prompt_stack_json: str | None = None,
        primary_prompt_json: str | None = None,
        workflow_prompts_json: str | None = None,
        prompt_stack_tokens: int | None = None,
        quality_score: float | None = None,
        user_feedback: str | None = None,
        parent_run_id: str | None = None,
        original_intent: str | None = None,
        resolved_intent: str | None = None,
    ):
        super().__init__()
        self.thread_id = thread_id
        self.document_ids = document_ids
        self._events: dict[str, dict] = {}   # run_id (str) → event data
        self.task_type = task_type
        self.route_intent = route_intent
        self.agent_name = agent_name
        self.prompt_name = prompt_name
        self.prompt_version = prompt_version
        self.base_prompt_name = base_prompt_name
        self.task_prompt_name = task_prompt_name
        self.quality_prompt_name = quality_prompt_name
        self.base_prompt_hash = base_prompt_hash
        self.task_prompt_hash = task_prompt_hash
        self.quality_prompt_hash = quality_prompt_hash
        self.prompt_stack_name = prompt_stack_name
        self.prompt_stack_json = prompt_stack_json
        self.primary_prompt_json = primary_prompt_json
        self.workflow_prompts_json = workflow_prompts_json
        self.prompt_stack_tokens = prompt_stack_tokens
        self.quality_score = quality_score
        self.user_feedback = user_feedback
        self.parent_run_id = parent_run_id
        self.original_intent = original_intent
        self.resolved_intent = resolved_intent
        self._tool_count = 0
        self._llm_call_count = 0
        self._root_run_id: str | None = None

    def _apply_metadata(self, metadata: dict | None) -> None:
        if not isinstance(metadata, dict):
            return
        self.task_type = self.task_type or metadata.get("task_type")
        self.route_intent = self.route_intent or metadata.get("route_intent")
        self.agent_name = self.agent_name or metadata.get("agent_name")
        self.prompt_name = self.prompt_name or metadata.get("prompt_name")
        self.prompt_version = self.prompt_version or metadata.get("prompt_version")
        self.base_prompt_name = self.base_prompt_name or metadata.get("base_prompt_name")
        self.task_prompt_name = self.task_prompt_name or metadata.get("task_prompt_name")
        self.quality_prompt_name = self.quality_prompt_name or metadata.get("quality_prompt_name")
        self.base_prompt_hash = self.base_prompt_hash or metadata.get("base_prompt_hash")
        self.task_prompt_hash = self.task_prompt_hash or metadata.get("task_prompt_hash")
        self.quality_prompt_hash = self.quality_prompt_hash or metadata.get("quality_prompt_hash")
        self.prompt_stack_name = self.prompt_stack_name or metadata.get("prompt_stack_name")
        self.prompt_stack_json = self.prompt_stack_json or metadata.get("prompt_stack_json")
        self.primary_prompt_json = self.primary_prompt_json or metadata.get("primary_prompt_json")
        self.workflow_prompts_json = self.workflow_prompts_json or metadata.get("workflow_prompts_json")
        self.prompt_stack_tokens = self.prompt_stack_tokens if self.prompt_stack_tokens is not None else metadata.get("prompt_stack_tokens")
        self.quality_score = self.quality_score if self.quality_score is not None else metadata.get("quality_score")
        self.user_feedback = self.user_feedback or metadata.get("user_feedback")
        self.original_intent = self.original_intent or metadata.get("original_intent")
        self.resolved_intent = self.resolved_intent or metadata.get("resolved_intent")

    def _ev(self, run_id) -> dict:
        rid = str(run_id)
        if rid not in self._events:
            self._events[rid] = {}
        return self._events[rid]

    # ── Chain ──────────────────────────────────────────────────────────────────

    def on_chain_start(self, serialized, inputs, *, run_id, parent_run_id=None, **kwargs):
        self._apply_metadata(kwargs.get("metadata"))
        rid = str(run_id)
        pid = str(parent_run_id) if parent_run_id else None
        if pid is None and self._root_run_id is None:
            self._root_run_id = rid
        s = serialized or {}
        name = s.get("name") or (s.get("id") or ["chain"])[-1]
        ev = self._ev(rid)
        ev.update({
            "run_id": rid,
            "parent_run_id": pid,
            "run_type": "chain",
            "name": name,
            "inputs": _safe_json(inputs),
            "start_time": _utcnow(),
        })

    def on_chain_end(self, outputs, *, run_id, **kwargs):
        rid = str(run_id)
        ev = self._ev(rid)
        ev.update({"outputs": _safe_json(outputs), "end_time": _utcnow()})
        if rid == self._root_run_id:
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
        if rid == self._root_run_id:
            self._flush(None)

    # ── LLM ────────────────────────────────────────────────────────────────────

    def on_chat_model_start(self, serialized, messages, *, run_id, parent_run_id=None, **kwargs):
        self._llm_call_count += 1
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
            "parent_run_id": str(parent_run_id) if parent_run_id else None,
            "run_type": "llm",
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
        self._tool_count += 1
        rid = str(run_id)
        ev = self._ev(rid)
        ev.update({
            "run_id": rid,
            "parent_run_id": str(parent_run_id) if parent_run_id else None,
            "run_type": "tool",
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
        """Serialize event data in memory, then schedule async DB write.

        The serialisation step is synchronous and fast (pure memory reads).
        The actual DB I/O runs in a thread pool via asyncio.to_thread so it
        never blocks the event loop.  Falls back to a synchronous write when
        there is no running event loop (e.g. test context).
        """
        rows = self._build_trace_rows(root_outputs)
        if not rows:
            return
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(asyncio.to_thread(LocalTracer._write_traces, rows))
        except RuntimeError:
            LocalTracer._write_traces(rows)

    def _build_trace_rows(self, root_outputs) -> list[dict]:
        """Build trace dicts from in-memory event data without any I/O."""
        doc_ids_json = json.dumps(self.document_ids) if self.document_ids else None

        display_json = None
        if root_outputs is not None:
            try:
                display_json = _safe_json(_build_display(root_outputs))
            except Exception as e:
                logger.warning("LocalTracer: display build failed: %s", e)

        total_prompt_tokens = sum(ev.get("prompt_tokens") or 0 for ev in self._events.values())
        total_completion_tokens = sum(ev.get("completion_tokens") or 0 for ev in self._events.values())

        rows = []
        for ev in self._events.values():
            if "run_id" not in ev or "run_type" not in ev:
                continue
            is_root = ev["run_id"] == self._root_run_id
            parent_id = ev.get("parent_run_id")
            if is_root and self.parent_run_id:
                parent_id = self.parent_run_id
            rows.append({
                "run_id": ev["run_id"],
                "parent_run_id": parent_id,
                "run_type": ev["run_type"],
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
                "display": display_json if is_root else None,
                "task_type": self.task_type,
                "route_intent": self.route_intent,
                "agent_name": self.agent_name,
                "prompt_name": self.prompt_name,
                "prompt_version": self.prompt_version,
                "base_prompt_name": self.base_prompt_name,
                "task_prompt_name": self.task_prompt_name,
                "quality_prompt_name": self.quality_prompt_name,
                "base_prompt_hash": self.base_prompt_hash,
                "task_prompt_hash": self.task_prompt_hash,
                "quality_prompt_hash": self.quality_prompt_hash,
                "prompt_stack_name": self.prompt_stack_name,
                "prompt_stack_json": self.prompt_stack_json,
                "primary_prompt_json": self.primary_prompt_json,
                "workflow_prompts_json": self.workflow_prompts_json,
                "prompt_stack_tokens": self.prompt_stack_tokens if is_root else None,
                "tool_count": self._tool_count if is_root else None,
                "llm_call_count": self._llm_call_count if is_root else None,
                "quality_score": self.quality_score if is_root else None,
                "user_feedback": self.user_feedback if is_root else None,
                "original_intent": self.original_intent if is_root else None,
                "resolved_intent": self.resolved_intent if is_root else None,
            })
        return rows

    @staticmethod
    def _write_traces(rows: list[dict]) -> None:
        """Synchronous DB write — intended to run inside asyncio.to_thread.

        Each row is flushed individually so that a run_id collision on one row
        only rolls back that row, not the entire batch.
        """
        from sqlalchemy.exc import IntegrityError
        from db import SessionLocal, Trace
        db = SessionLocal()
        written = 0
        try:
            for row in rows:
                try:
                    db.add(Trace(**row))
                    db.flush()
                    written += 1
                except IntegrityError:
                    db.rollback()
                    # run_id already exists — update the existing row instead.
                    run_id = row.get("run_id")
                    if run_id:
                        existing = db.query(Trace).filter(Trace.run_id == run_id).first()
                        if existing:
                            for k, v in row.items():
                                if k != "run_id" and v is not None:
                                    setattr(existing, k, v)
                            db.flush()
                            written += 1
            db.commit()
            logger.debug("LocalTracer: flushed %d/%d events", written, len(rows))
        except Exception as e:
            logger.error("LocalTracer: flush failed: %s", e)
            db.rollback()
        finally:
            db.close()
