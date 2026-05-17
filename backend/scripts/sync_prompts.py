"""Sync local prompt .txt files to Langfuse with the 'production' label.

Usage:
    python scripts/sync_prompts.py                                    # sync all prompts
    python scripts/sync_prompts.py task_planner reflector             # sync specific prompts (partial name match)
    python scripts/sync_prompts.py --changed-only                     # only upload prompts whose content changed
    python scripts/sync_prompts.py --changed-only task_planner        # changed-only + name filter
    python scripts/sync_prompts.py --list                             # list available prompt files
"""
import os
import sys
from langfuse import get_client
from dotenv import load_dotenv

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
load_dotenv()

PROMPTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "prompts"))


def _resolve_files(names: list[str]) -> list[str]:
    """Return matching filenames from PROMPTS_DIR. Supports partial name match."""
    all_files = sorted(f for f in os.listdir(PROMPTS_DIR) if f.endswith(".txt"))
    if not names:
        return all_files
    result = []
    for name in names:
        matched = [f for f in all_files if name.lower() in f.lower()]
        if not matched:
            print(f"  [warn] No prompt file matching '{name}'")
        result.extend(f for f in matched if f not in result)
    return result


def _fetch_remote_content(langfuse, prompt_name: str) -> str | None:
    """Fetch the current production prompt content from Langfuse. Returns None if not found."""
    try:
        remote = langfuse.get_prompt(prompt_name, label="production", cache_ttl_seconds=0)
        return remote.prompt if isinstance(remote.prompt, str) else None
    except Exception:
        return None


def sync_prompts(names: list[str] | None = None, changed_only: bool = False) -> None:
    if not os.path.exists(PROMPTS_DIR):
        print(f"Error: Prompts directory not found at {PROMPTS_DIR}")
        return

    files = _resolve_files(names or [])
    if not files:
        print("No matching prompt files found.")
        return

    langfuse = get_client()
    mode = "changed prompts only" if changed_only else "all prompts"
    print(f"Syncing {len(files)} prompt(s) to Langfuse ({mode})...\n")

    ok = skipped = err = 0
    for filename in files:
        prompt_name = os.path.splitext(filename)[0]
        file_path = os.path.join(PROMPTS_DIR, filename)

        with open(file_path, encoding="utf-8") as f:
            local_content = f.read()

        if changed_only:
            remote_content = _fetch_remote_content(langfuse, prompt_name)
            if remote_content is not None and remote_content.strip() == local_content.strip():
                print(f"  — {prompt_name} (unchanged, skipped)")
                skipped += 1
                continue

        try:
            langfuse.create_prompt(
                name=prompt_name,
                prompt=local_content,
                type="text",
                labels=["production"],
            )
            print(f"  ✓ {prompt_name}")
            ok += 1
        except Exception as e:
            print(f"  ✗ {prompt_name}: {e}")
            err += 1

    summary = f"{ok} uploaded"
    if skipped:
        summary += f", {skipped} unchanged"
    if err:
        summary += f", {err} failed"
    print(f"\nDone: {summary}.")


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--list" in args:
        files = sorted(f for f in os.listdir(PROMPTS_DIR) if f.endswith(".txt"))
        print("\n".join(os.path.splitext(f)[0] for f in files))
    else:
        changed_only = "--changed-only" in args
        names = [a for a in args if not a.startswith("--")]
        sync_prompts(names if names else None, changed_only=changed_only)
