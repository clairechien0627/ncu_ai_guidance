"""Sync local prompt .txt files to Langfuse with the 'production' label.

Usage:
    python scripts/sync_prompts.py                          # sync all prompts
    python scripts/sync_prompts.py task_planner reflector  # sync specific prompts (partial name match)
    python scripts/sync_prompts.py --list                  # list available prompt files
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


def sync_prompts(names: list[str] | None = None) -> None:
    if not os.path.exists(PROMPTS_DIR):
        print(f"Error: Prompts directory not found at {PROMPTS_DIR}")
        return

    files = _resolve_files(names or [])
    if not files:
        print("No matching prompt files found.")
        return

    langfuse = get_client()
    print(f"Syncing {len(files)} prompt(s) to Langfuse...\n")

    ok = err = 0
    for filename in files:
        prompt_name = os.path.splitext(filename)[0]
        file_path = os.path.join(PROMPTS_DIR, filename)

        with open(file_path, encoding="utf-8") as f:
            content = f.read()

        try:
            langfuse.create_prompt(
                name=prompt_name,
                prompt=content,
                type="text",
                labels=["production"],
            )
            print(f"  ✓ {prompt_name}")
            ok += 1
        except Exception as e:
            print(f"  ✗ {prompt_name}: {e}")
            err += 1

    print(f"\nDone: {ok} succeeded, {err} failed.")


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--list" in args:
        files = sorted(f for f in os.listdir(PROMPTS_DIR) if f.endswith(".txt"))
        print("\n".join(os.path.splitext(f)[0] for f in files))
    else:
        sync_prompts(args if args else None)
