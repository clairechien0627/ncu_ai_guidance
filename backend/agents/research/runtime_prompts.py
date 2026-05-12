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
    return {
        "prompt_stack_name": RESEARCH_BASE_STACK,
        "prompt_stack_json": json.dumps(
            [
                {
                    "name": prompt.name,
                    "base_name": prompt.base_name,
                    "source_name": prompt.source_name,
                    "version": prompt.version,
                }
            ],
            ensure_ascii=False,
        ),
        "prompt_stack_tokens": max(1, len(prompt.content) // 4),
        "base_prompt_name": prompt.name,
        "base_prompt_hash": prompt.version,
        "prompt_name": prompt.name,
        "prompt_version": prompt.version,
    }
