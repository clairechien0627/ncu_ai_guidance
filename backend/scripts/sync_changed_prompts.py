"""Push locally changed prompts to Langfuse as new versions.

Changed = local file content differs from the current Langfuse production version.
Only pushes prompts that have actually changed, so it's safe to run repeatedly.

Usage:
    python backend/scripts/sync_changed_prompts.py          # only changed prompts 自動偵測內容有變的才推
    python backend/scripts/sync_changed_prompts.py --all    # force-push all 強制全部推
    python backend/scripts/sync_changed_prompts.py chat_mode research_writer  # specific 只推特定幾個
    python backend/scripts/sync_changed_prompts.py --dry-run  # 先看看會推哪些，不實際寫入
"""
import argparse
import hashlib
import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from dotenv import load_dotenv
load_dotenv(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".env")))

PROMPTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "prompts"))


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _local_content(name: str) -> str | None:
    path = os.path.join(PROMPTS_DIR, f"{name}.txt")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return f.read()


def _langfuse_content(langfuse, name: str) -> str | None:
    try:
        lp = langfuse.get_prompt(name, label="production")
        if getattr(lp, "type", "text") == "chat":
            return "\n\n".join(
                f"[{m.get('role', 'user')}]: {m.get('content', '')}"
                if isinstance(m, dict) else str(m)
                for m in lp.prompt
            )
        return lp.prompt
    except Exception:
        return None


def all_local_names() -> list[str]:
    if not os.path.isdir(PROMPTS_DIR):
        return []
    return sorted(
        os.path.splitext(f)[0]
        for f in os.listdir(PROMPTS_DIR)
        if f.endswith(".txt")
    )


def sync_one(langfuse, name: str, force: bool = False, dry_run: bool = False) -> str:
    """Returns: 'pushed', 'skipped', 'missing'."""
    local = _local_content(name)
    if local is None:
        print(f"  skip '{name}' — not found locally")
        return "missing"

    if not force:
        remote = _langfuse_content(langfuse, name)
        if remote is not None and _sha(local) == _sha(remote):
            print(f"  = '{name}' unchanged, skip")
            return "skipped"

    if dry_run:
        print(f"  would push '{name}'")
        return "pushed"

    try:
        langfuse.create_prompt(
            name=name,
            prompt=local,
            type="text",
            labels=["production"],
        )
        print(f"  OK '{name}' pushed to Langfuse (production)")
        return "pushed"
    except Exception as e:
        print(f"  ERROR '{name}' error: {e}")
        return "error"


def main():
    parser = argparse.ArgumentParser(description="Sync local prompts → Langfuse")
    parser.add_argument("names", nargs="*", help="Prompt names to sync (default: detect changed)")
    parser.add_argument("--all", dest="force_all", action="store_true", help="Push all prompts regardless of diff")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be pushed without writing")
    args = parser.parse_args()

    from langfuse import get_client
    langfuse = get_client()

    names = args.names if args.names else all_local_names()
    label = "all" if args.force_all else "changed"
    print(f"Checking {len(names)} prompt(s) for {label} content...\n")

    counts = {"pushed": 0, "skipped": 0, "missing": 0, "error": 0}
    for name in names:
        result = sync_one(langfuse, name, force=args.force_all, dry_run=args.dry_run)
        counts[result] = counts.get(result, 0) + 1

    print(f"\nDone — pushed: {counts['pushed']}, unchanged: {counts['skipped']}, "
          f"missing: {counts['missing']}, errors: {counts.get('error', 0)}")


if __name__ == "__main__":
    main()
