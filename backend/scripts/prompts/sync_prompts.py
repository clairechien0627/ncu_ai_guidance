"""Sync local prompt .txt files to Langfuse with the 'production' label.

Default behaviour: only push prompts whose content has changed (SHA comparison).

Usage:
    python scripts/prompts/sync_prompts.py                        # sync changed prompts
    python scripts/prompts/sync_prompts.py task_planner reflector # sync specific (partial name match)
    python scripts/prompts/sync_prompts.py --all                  # force-push all
    python scripts/prompts/sync_prompts.py --dry-run              # preview without writing
    python scripts/prompts/sync_prompts.py --list                 # list available prompt files
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from dotenv import load_dotenv
load_dotenv(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".env")))

PROMPTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "prompts"))


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _local_names() -> list[str]:
    if not os.path.isdir(PROMPTS_DIR):
        return []
    return sorted(os.path.splitext(f)[0] for f in os.listdir(PROMPTS_DIR) if f.endswith(".txt"))


def _resolve_names(names: list[str]) -> list[str]:
    """Support partial name matching."""
    all_names = _local_names()
    if not names:
        return all_names
    result: list[str] = []
    for name in names:
        matched = [n for n in all_names if name.lower() in n.lower()]
        if not matched:
            print(f"  [warn] No prompt matching '{name}'")
        result.extend(n for n in matched if n not in result)
    return result


def _local_content(name: str) -> str | None:
    path = os.path.join(PROMPTS_DIR, f"{name}.txt")
    return open(path, encoding="utf-8").read() if os.path.exists(path) else None


def _remote_content(langfuse, name: str) -> str | None:
    try:
        lp = langfuse.get_prompt(name, label="production", cache_ttl_seconds=0)
        if getattr(lp, "type", "text") == "chat":
            return "\n\n".join(
                f"[{m.get('role', 'user')}]: {m.get('content', '')}"
                if isinstance(m, dict) else str(m)
                for m in lp.prompt
            )
        return lp.prompt if isinstance(lp.prompt, str) else None
    except Exception:
        return None


def sync_one(langfuse, name: str, *, force: bool = False, dry_run: bool = False) -> str:
    """Returns: 'pushed' | 'skipped' | 'missing' | 'error'."""
    local = _local_content(name)
    if local is None:
        print(f"  — {name} (not found locally, skipped)")
        return "missing"

    if not force:
        remote = _remote_content(langfuse, name)
        if remote is not None and _sha(local) == _sha(remote):
            print(f"  = {name} (unchanged)")
            return "skipped"

    if dry_run:
        print(f"  ➜ {name} (would push)")
        return "pushed"

    try:
        langfuse.create_prompt(name=name, prompt=local, type="text", labels=["production"])
        print(f"  ✓ {name}")
        return "pushed"
    except Exception as e:
        print(f"  ✗ {name}: {e}")
        return "error"


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync local prompts → Langfuse (production)")
    parser.add_argument("names", nargs="*", help="Prompt names to sync (supports partial match)")
    parser.add_argument("--all", dest="force_all", action="store_true", help="Force-push all, ignore diff")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing to Langfuse")
    parser.add_argument("--list", action="store_true", help="List available local prompt files")
    args = parser.parse_args()

    if args.list:
        for name in _local_names():
            print(name)
        return

    from langfuse import get_client
    langfuse = get_client()

    names = _resolve_names(args.names)
    if not names:
        print("No matching prompt files found.")
        return

    mode = "force-all" if args.force_all else "changed only"
    suffix = " (dry run)" if args.dry_run else ""
    print(f"Syncing {len(names)} prompt(s) [{mode}]{suffix}...\n")

    counts: dict[str, int] = {}
    for name in names:
        result = sync_one(langfuse, name, force=args.force_all, dry_run=args.dry_run)
        counts[result] = counts.get(result, 0) + 1

    parts = []
    if counts.get("pushed"):
        parts.append(f"{counts['pushed']} pushed")
    if counts.get("skipped"):
        parts.append(f"{counts['skipped']} unchanged")
    if counts.get("missing"):
        parts.append(f"{counts['missing']} missing")
    if counts.get("error"):
        parts.append(f"{counts['error']} failed")
    print(f"\nDone — {', '.join(parts)}.")


if __name__ == "__main__":
    main()
