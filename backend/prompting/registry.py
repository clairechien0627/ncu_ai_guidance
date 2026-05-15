"""Canonical prompt registry with legacy-name compatibility."""

from __future__ import annotations

import functools
import hashlib
import json
import logging
import os
from dataclasses import dataclass

from config import settings

_PROMPTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "prompts"))
_EXTENDS_PREFIX = "# extends:"
_cache: dict[str, str] = {}
_hash_cache: dict[str, str] = {}              # source_name → sha256[:12] hash
_langfuse_obj_cache: dict[str, object] = {}   # source_name → Langfuse PromptClient
_langfuse_version_cache: dict[str, int] = {}  # source_name → Langfuse version number
logger = logging.getLogger(__name__)


ALIASES: dict[str, str] = {
    "core": "core",
    "retrieval_capability": "retrieval_capability",
    "chat_mode": "chat_mode",
    "summary_quality": "summary_quality",
    "question_skill": "question_skill",
    "summary_structure": "summary_structure",
    "question_generator": "question_generator",
    "intent_router": "intent_router",
    "route_coordinator": "route_coordinator",
    "evaluation_agent": "evaluation_agent",
    "task_planner": "task_planner",
    "research_orchestrator": "research_orchestrator",
    "research_planner": "research_planner",
    "research_reflector": "research_reflector",
    "research_writer": "research_writer",
    "chat": "chat_mode",
    "question_system": "question_generator",
    "router": "route_coordinator",
    "base_research": "retrieval_capability",
    "chat_task": "chat_mode",
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
    if settings.langfuse_enabled and settings.langfuse_public_key.get_secret_value() and settings.langfuse_secret_key.get_secret_value():
        base_url = (
            os.getenv("LANGFUSE_BASE_URL")
            or settings.langfuse_base_url
            or settings.langfuse_host
        )
        if base_url:
            os.environ.setdefault("LANGFUSE_BASE_URL", base_url)

        try:
            from langfuse import get_client

            langfuse = get_client()
            # Try production first, then fall back to latest if production is missing
            lp = None
            try:
                lp = langfuse.get_prompt(src, label="production")
            except Exception:
                pass

            if not lp:
                try:
                    lp = langfuse.get_prompt(src)  # default is latest
                    if lp:
                        logger.info(f"Prompt '{src}' found in Langfuse (latest), but missing 'production' label.")
                except Exception:
                    pass

            if lp:
                # If it's a chat prompt, join messages with newlines for legacy compatibility
                prompt_type = getattr(lp, "type", "text")
                if prompt_type == "chat":
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
                logger.info(f"Successfully loaded prompt '{src}' from Langfuse (v{getattr(lp, 'version', '?')})")
                return content
            else:
                logger.warning(f"Prompt '{src}' not found in Langfuse. Falling back to local file.")
        except Exception as e:
            logger.warning(f"Langfuse prompt fetch error for '{src}': {e}")

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
    _hash_cache.clear()
    _langfuse_obj_cache.clear()
    _langfuse_version_cache.clear()
    _ab_tests_cached.cache_clear()


def version(name: str) -> str:
    src = source_name(name)
    get(name)  # ensure caches are populated
    lf_ver = _langfuse_version_cache.get(src)
    if lf_ver is not None:
        return f"langfuse:{lf_ver}"
    if src not in _hash_cache:
        content = _cache.get(src, "")
        _hash_cache[src] = hashlib.sha256(content.encode("utf-8")).hexdigest()[:12]
    return f"sha256:{_hash_cache[src]}"


@functools.lru_cache(maxsize=8)
def _ab_tests_cached(raw: str) -> dict[str, list[str]]:
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


def _ab_tests() -> dict[str, list[str]]:
    return _ab_tests_cached(os.getenv("PROMPT_AB_TESTS", "").strip())


def select(name: str, key: str | None = None) -> str:
    variants = _ab_tests().get(name)
    if not variants:
        return name
    bucket_key = key or name
    digest = hashlib.sha256(f"{name}:{bucket_key}".encode("utf-8")).hexdigest()
    return variants[int(digest[:8], 16) % len(variants)]


def get_langfuse_obj(name: str):
    """Return the raw Langfuse PromptClient for a prompt, or None if not in Langfuse cache.

    The observability layer uses this object to link the active Langfuse
    generation to the specific prompt version.
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
