from __future__ import annotations

import json

from langchain_core.messages import SystemMessage

from prompting.registry import get as get_prompt
from prompting.registry import resolve


RESEARCH_BASE_STACK = "research_core"
RESEARCH_CORE_PROMPT_NAME = "core"


def research_node_system_prompt(node_prompt_name: str) -> str:
    """Compose the effective system prompt for a research runtime node."""
    parts = [
        get_prompt(RESEARCH_CORE_PROMPT_NAME),
        get_prompt(node_prompt_name),
    ]
    return "\n\n".join(part.strip() for part in parts if part and part.strip())


def research_node_system_messages(node_prompt_name: str) -> list[SystemMessage]:
    """Return one SystemMessage per prompt file for trace readability."""
    return [
        SystemMessage(content=get_prompt(RESEARCH_CORE_PROMPT_NAME)),
        SystemMessage(content=get_prompt(node_prompt_name)),
    ]


def research_base_stack_metadata() -> dict:
    prompt = resolve(RESEARCH_CORE_PROMPT_NAME)
    prompt_json = {
        "name": prompt.name,
        "base_name": prompt.base_name,
        "source_name": prompt.source_name,
        "version": prompt.version,
    }
    stack_json = json.dumps([prompt_json], ensure_ascii=False)
    return {
        "prompt_stack_name": RESEARCH_BASE_STACK,
        "prompt_stack_json": stack_json,
        "primary_prompt_json": json.dumps(prompt_json, ensure_ascii=False),
        "workflow_prompts_json": stack_json,
        "prompt_stack_tokens": max(1, len(prompt.content) // 4),
        "base_prompt_name": prompt.name,
        "base_prompt_hash": prompt.version,
        "prompt_name": prompt.name,
        "prompt_version": prompt.version,
    }


def research_node_stack_metadata(node_prompt_name: str) -> dict:
    core = resolve(RESEARCH_CORE_PROMPT_NAME)
    node = resolve(node_prompt_name)
    prompts = [core, node]
    prompt_json = [
        {
            "name": prompt.name,
            "base_name": prompt.base_name,
            "source_name": prompt.source_name,
            "version": prompt.version,
        }
        for prompt in prompts
    ]
    primary_prompt_json = {
        "name": node.name,
        "base_name": node.base_name,
        "source_name": node.source_name,
        "version": node.version,
    }
    stack_json = json.dumps(prompt_json, ensure_ascii=False)
    return {
        "prompt_stack_name": "research_runtime",
        "prompt_stack_json": stack_json,
        "primary_prompt_json": json.dumps(primary_prompt_json, ensure_ascii=False),
        "workflow_prompts_json": stack_json,
        "prompt_stack_tokens": max(1, len("\n".join(prompt.content for prompt in prompts)) // 4),
        "base_prompt_name": core.name,
        "base_prompt_hash": core.version,
        "prompt_name": node.name,
        "prompt_version": node.version,
    }
