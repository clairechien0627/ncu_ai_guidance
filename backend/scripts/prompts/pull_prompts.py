"""Pull Langfuse production prompt versions back to local .txt files.

Fetches prompts that have a 'production' label in Langfuse and overwrites
the corresponding local .txt file. New prompts created in Langfuse UI that
don't exist locally will also be written.

Usage:
    python backend/scripts/pull_prompts.py               # pull all known prompts 拉全部
    python backend/scripts/pull_prompts.py chat_mode     # pull specific prompt(s) 只拉特定的
    python backend/scripts/pull_prompts.py --dry-run     # show what would change 先預覽
"""
import argparse
import hashlib
import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from dotenv import load_dotenv
load_dotenv()

PROMPTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "prompts"))


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _chat_to_text(messages: list) -> str:
    return "\n\n".join(
        f"[{m.get('role', 'user')}]: {m.get('content', '')}"
        if isinstance(m, dict) else str(m)
        for m in messages
    )


def all_names(langfuse) -> list[str]:
    """Union of local file names and Langfuse prompt names."""
    names = set()

    if os.path.isdir(PROMPTS_DIR):
        for f in os.listdir(PROMPTS_DIR):
            if f.endswith(".txt"):
                names.add(os.path.splitext(f)[0])

    # Try Langfuse list API
    try:
        page = langfuse.client.prompts.list()
        for p in (page.data or []):
            names.add(p.name)
    except Exception:
        pass  # use local names only if list API unavailable

    return sorted(names)


def pull_one(langfuse, name: str, dry_run: bool = False) -> str:
    """Returns: 'updated', 'created', 'unchanged', 'not_in_langfuse', 'error'."""
    try:
        lp = langfuse.get_prompt(name, label="production")
    except Exception:
        print(f"  - '{name}' not found in Langfuse, keeping local")
        return "not_in_langfuse"

    content = _chat_to_text(lp.prompt) if lp.type == "chat" else lp.prompt

    local_path = os.path.join(PROMPTS_DIR, f"{name}.txt")
    existed = os.path.exists(local_path)

    if existed:
        with open(local_path, encoding="utf-8") as f:
            local_content = f.read()
        if _sha(local_content) == _sha(content):
            print(f"  = '{name}' already up to date (Langfuse v{lp.version})")
            return "unchanged"

    if dry_run:
        action = "update" if existed else "create"
        print(f"  would {action} '{name}' ← Langfuse v{lp.version}")
        return "updated" if existed else "created"

    os.makedirs(PROMPTS_DIR, exist_ok=True)
    with open(local_path, "w", encoding="utf-8") as f:
        f.write(content)

    if existed:
        print(f"  ✓ '{name}' updated from Langfuse v{lp.version}")
        return "updated"
    else:
        print(f"  + '{name}' created from Langfuse v{lp.version}")
        return "created"


def main():
    parser = argparse.ArgumentParser(description="Pull Langfuse production prompts → local files")
    parser.add_argument("names", nargs="*", help="Specific prompt names (default: all known)")
    parser.add_argument("--dry-run", action="store_true", help="Show changes without writing")
    args = parser.parse_args()

    from langfuse import get_client
    langfuse = get_client()

    names = args.names if args.names else all_names(langfuse)
    suffix = " (dry run)" if args.dry_run else ""
    print(f"Pulling {len(names)} prompt(s) from Langfuse{suffix}...\n")

    counts: dict[str, int] = {}
    for name in names:
        result = pull_one(langfuse, name, dry_run=args.dry_run)
        counts[result] = counts.get(result, 0) + 1

    print(f"\nDone — updated: {counts.get('updated', 0)}, created: {counts.get('created', 0)}, "
          f"unchanged: {counts.get('unchanged', 0)}, not in Langfuse: {counts.get('not_in_langfuse', 0)}")


if __name__ == "__main__":
    main()
