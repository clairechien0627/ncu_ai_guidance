"""Prompt and metadata helpers for research agent runtime."""

import json

from prompting.registry import resolve


RESEARCH_AGENT_NAME = "research"
SUMMARY_AGENT_NAME = "research"
RESEARCH_STACK_NAME = "research_runtime"

RUNTIME_PROMPT_NAMES = (
    "task_planner",
    "research_scheduler",
    "research_planner",
    "research_reflector",
    "research_writer",
)


def trace_metadata(
    thread_id: str | None = None,
    document_ids: list[int] | None = None,
    *,
    agent_name: str = SUMMARY_AGENT_NAME,
    stack_name: str = RESEARCH_STACK_NAME,
) -> dict[str, str | int]:
    """Build trace metadata for research or summary runs."""
    from prompting.loader import load_stack as _load_stack

    def _prompt_key(tid: str | None, doc_ids: list[int] | None) -> str:
        doc_key = ",".join(str(d) for d in sorted(doc_ids or []))
        return f"{tid or ''}:{doc_key}"

    stack = _load_stack(stack_name, _prompt_key(thread_id, document_ids))
    return {
        "agent_name": agent_name,
        **stack.metadata(),
    }


def runtime_prompt_specs() -> list[dict[str, str]]:
    specs: list[dict[str, str]] = []
    for name in RUNTIME_PROMPT_NAMES:
        spec = resolve(name)
        specs.append({
            "name": spec.name,
            "base_name": spec.base_name,
            "source_name": spec.source_name,
            "version": spec.version,
        })
    return specs


def compact_prompt_summary(specs: list[dict[str, str]]) -> str:
    return "、".join(
        f"{item['name']}({item['version']})"
        for item in specs
    )


def parse_json_field(value):
    if not value:
        return None
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except Exception:
        return value


def graph_runtime_metadata(metadata: dict) -> dict:
    keep = (
        "agent_name",
        "prompt_stack_name",
        "primary_prompt_json",
        "workflow_prompts_json",
        "prompt_stack_tokens",
        "base_prompt_name",
        "base_prompt_hash",
        "prompt_name",
        "prompt_version",
        "task_prompt_name",
        "task_prompt_hash",
        "document_id",
        "type",
        "research_effective_base_stack_name",
        "research_runtime_prompt_summary",
    )
    return {key: metadata[key] for key in keep if key in metadata}
