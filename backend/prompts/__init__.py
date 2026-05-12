"""Backward-compatible prompt API.

New code should import from ``prompting.registry`` or ``prompting.loader``.
This module remains so older code using ``import prompts`` keeps working.
"""

from prompting.registry import (
    PromptSpec,
    exists,
    get,
    list_known_names,
    reload,
    reload_all,
    resolve,
    select,
    source_name,
    version,
)

__all__ = [
    "PromptSpec",
    "exists",
    "get",
    "list_known_names",
    "reload",
    "reload_all",
    "resolve",
    "select",
    "source_name",
    "version",
]
