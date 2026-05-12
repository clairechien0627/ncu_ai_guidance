"""Canonical prompt registry with legacy-name compatibility."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass

_PROMPTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "prompts"))
_EXTENDS_PREFIX = "# extends:"
_cache: dict[str, str] = {}
_langfuse_obj_cache: dict[str, object] = {}   # source_name → Langfuse PromptClient
_langfuse_version_cache: dict[str, int] = {}  # source_name → Langfuse version number


ALIASES: dict[str, str] = {
    "core": "core",
    "retrieval_capability": "retrieval_capability",
    "chat_mode": "chat_mode",
    "summary_mode": "summary_mode",
    "summary_quality": "summary_quality",
    "question_skill": "question_skill",
    "summary_structure": "summary_structure",
    "question_generator": "question_generator",
    "intent_router": "intent_router",
    "task_planner": "task_planner",
    "research_planner": "research_planner",
    "research_reflector": "research_reflector",
    "research_writer": "research_writer",
    "chat": "chat_mode",
    "summary_agent": "summary_mode",
    "question_system": "question_generator",
    "router": "intent_router",
    "base_research": "retrieval_capability",
    "chat_task": "chat_mode",
    "summary_task": "summary_mode",
    "question_task": "question_skill",
}


@dataclass(frozen=True)
class PromptSpec:
    base_name: str
    name: str
    content: str
    version: str
    source_name: str
    langfuse_version: int | None = None  # Langfuse version number (None if loaded from local file)


def canonical_name(name: str) -> str:
    return str(name).strip()


def source_name(name: str) -> str:
    prompt_name = canonical_name(name)
    return ALIASES.get(prompt_name, prompt_name)


def _path_for(name: str) -> str:
    return os.path.join(_PROMPTS_DIR, f"{source_name(name)}.txt")


def exists(name: str) -> bool:
    return os.path.exists(_path_for(name))


def _load_prompt(name: str, stack: list[str]) -> str:
    src = source_name(name)
    if src in stack:
        chain = " -> ".join([*stack, src])
        raise ValueError(f"Prompt extends cycle detected: {chain}")

    path = os.path.join(_PROMPTS_DIR, f"{src}.txt")
    if not os.path.exists(path):
        raise FileNotFoundError(f"Prompt file not found: {path}")
    with open(path, encoding="utf-8") as f:
        content = f.read()

    lines = content.splitlines()
    if not lines:
        return content
    first = lines[0].strip()
    if not first.lower().startswith(_EXTENDS_PREFIX):
        return content

    parent = first[len(_EXTENDS_PREFIX):].strip()
    if not parent:
        raise ValueError(f"Prompt {src} has an empty extends target")
    body = "\n".join(lines[1:]).lstrip()
    return f"{_load_prompt(parent, [*stack, src]).rstrip()}\n\n{body}".rstrip() + "\n"


def get(name: str) -> str:
    src = source_name(name)
    if src in _cache:
        return _cache[src]

    # Try Langfuse first
    try:
        from langfuse import get_client
        langfuse = get_client()
        # Fetch with 'production' label by default
        lp = langfuse.get_prompt(src, label="production")
        if lp:
            # If it's a chat prompt, join messages with newlines for legacy compatibility
            if lp.type == "chat":
                content = "\n\n".join(
                    f"[{m.get('role', 'user')}]: {m.get('content', '')}"
                    if isinstance(m, dict) else str(m)
                    for m in lp.prompt
                )
            else:
                content = lp.prompt

            _cache[src] = content
            _langfuse_obj_cache[src] = lp
            _langfuse_version_cache[src] = getattr(lp, "version", None)
            return content
    except Exception:
        # Fallback to local file if Langfuse is unavailable or prompt doesn't exist
        pass

    if src not in _cache:
        _cache[src] = _load_prompt(src, [])
    return _cache[src]


def reload(name: str) -> str:
    src = source_name(name)
    _cache.pop(src, None)
    _langfuse_obj_cache.pop(src, None)
    _langfuse_version_cache.pop(src, None)
    return get(name)


def reload_all() -> None:
    _cache.clear()
    _langfuse_obj_cache.clear()
    _langfuse_version_cache.clear()


def version(name: str) -> str:
    src = source_name(name)
    get(name)  # ensure caches are populated
    lf_ver = _langfuse_version_cache.get(src)
    if lf_ver is not None:
        return f"langfuse:{lf_ver}"
    content = _cache.get(src, "")
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()[:12]
    return f"sha256:{digest}"


def _ab_tests() -> dict[str, list[str]]:
    raw = os.getenv("PROMPT_AB_TESTS", "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except Exception:
        return {}
    if not isinstance(parsed, dict):
        return {}

    result: dict[str, list[str]] = {}
    for base_name, variants in parsed.items():
        if isinstance(variants, str):
            names = [v.strip() for v in variants.split(",")]
        elif isinstance(variants, list):
            names = [str(v).strip() for v in variants]
        else:
            names = []
        valid = [name for name in names if name and exists(name)]
        if valid:
            key_names = {str(base_name)}
            base_source = source_name(str(base_name))
            for alias, source in ALIASES.items():
                if alias == base_name or source == base_source:
                    key_names.add(alias)
            for key_name in key_names:
                result[key_name] = valid
    return result


def select(name: str, key: str | None = None) -> str:
    variants = _ab_tests().get(name)
    if not variants:
        return name
    bucket_key = key or name
    digest = hashlib.sha256(f"{name}:{bucket_key}".encode("utf-8")).hexdigest()
    return variants[int(digest[:8], 16) % len(variants)]


def get_langfuse_obj(name: str):
    """Return the raw Langfuse PromptClient for a prompt, or None if not in Langfuse cache.

    Useful for passing as ``metadata={"langfuse_prompt": get_langfuse_obj(...)}`` to a
    LangChain prompt template so Langfuse links traces to the specific prompt version.
    """
    src = source_name(name)
    if src not in _langfuse_obj_cache:
        get(name)  # trigger Langfuse fetch
    return _langfuse_obj_cache.get(src)


def resolve(name: str, key: str | None = None) -> PromptSpec:
    base_name = canonical_name(name)
    active_name = select(base_name, key)
    src = source_name(active_name)
    return PromptSpec(
        base_name=base_name,
        name=active_name,
        content=get(active_name),
        version=version(active_name),
        source_name=src,
        langfuse_version=_langfuse_version_cache.get(src),
    )


def list_known_names() -> list[str]:
    file_names = []
    if os.path.isdir(_PROMPTS_DIR):
        file_names = [
            os.path.splitext(fname)[0]
            for fname in os.listdir(_PROMPTS_DIR)
            if fname.endswith(".txt")
        ]
    return sorted(set(file_names) | set(ALIASES))
