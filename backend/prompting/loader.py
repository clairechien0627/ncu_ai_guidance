"""Prompt stack loader."""

from __future__ import annotations

import json
from dataclasses import dataclass

from . import registry


PROMPT_STACKS: dict[str, list[str]] = {
    "chat_default": ["core", "retrieval_capability", "chat_mode"],
    "chat_summary": [
        "core",
        "retrieval_capability",
        "chat_mode",
        "summary_mode",
        "summary_quality",
    ],
    "chat_question": ["core", "retrieval_capability", "chat_mode", "question_skill"],
    "retrieval_default": ["core", "retrieval_capability"],
    "research_summary": ["core", "retrieval_capability", "summary_mode", "summary_quality"],
    "research_runtime": [
        "core",
        "task_planner",
        "research_planner",
        "research_reflector",
        "research_writer",
    ],
    "extract_step2": ["core", "summary_structure"],
    "extract_step3": ["core", "question_generator"],
}

STACK_ALIASES: dict[str, str] = {
    "extract_step1": "research_summary",
}

PRIMARY_PROMPT_BY_STACK: dict[str, str] = {
    "chat_default": "chat_mode",
    "chat_summary": "summary_mode",
    "chat_question": "question_skill",
    "retrieval_default": "retrieval_capability",
    "research_summary": "summary_mode",
    "research_runtime": "research_writer",
    "extract_step2": "summary_structure",
    "extract_step3": "question_generator",
}


@dataclass(frozen=True)
class PromptStack:
    name: str
    prompts: list[registry.PromptSpec]

    @property
    def contents(self) -> list[str]:
        return [prompt.content for prompt in self.prompts if prompt.content]

    @property
    def tokens_estimate(self) -> int:
        text = "\n".join(self.contents)
        if not text:
            return 0
        return max(1, len(text) // 4)

    def metadata(self) -> dict[str, str | int]:
        prompts = [
            {
                "name": prompt.name,
                "base_name": prompt.base_name,
                "source_name": prompt.source_name,
                "version": prompt.version,
            }
            for prompt in self.prompts
        ]
        data: dict[str, str | int] = {
            "prompt_stack_name": self.name,
            "prompt_stack_json": json.dumps(prompts, ensure_ascii=False),
            "prompt_stack_tokens": self.tokens_estimate,
        }
        if self.prompts:
            primary_name = PRIMARY_PROMPT_BY_STACK.get(self.name)
            primary = next(
                (prompt for prompt in self.prompts if prompt.base_name == primary_name),
                self.prompts[-1],
            )
            data["base_prompt_name"] = self.prompts[0].name
            data["base_prompt_hash"] = self.prompts[0].version
            data["prompt_name"] = primary.name
            data["prompt_version"] = primary.version
        if len(self.prompts) >= 2:
            data["task_prompt_name"] = data["prompt_name"]
            data["task_prompt_hash"] = data["prompt_version"]
        for prompt in self.prompts:
            if prompt.base_name == "summary_quality" or prompt.source_name == "summary_quality":
                data["quality_prompt_name"] = prompt.name
                data["quality_prompt_hash"] = prompt.version
                break
        return data


def load_stack(name: str, key: str | None = None) -> PromptStack:
    stack_name = STACK_ALIASES.get(name, name)
    if stack_name not in PROMPT_STACKS:
        raise KeyError(f"Unknown prompt stack: {name}")
    prompts = [registry.resolve(prompt_name, key) for prompt_name in PROMPT_STACKS[stack_name]]
    return PromptStack(name=stack_name, prompts=prompts)
