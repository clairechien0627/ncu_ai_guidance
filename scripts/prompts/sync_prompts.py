"""Sync local prompt .txt files into the prompt_versions DB table.

Usage:
    cd backend
    python ../scripts/prompts/sync_prompts.py

Each .txt file in backend/prompts/ is hashed (SHA-256). If this (name, hash)
pair is not yet in prompt_versions, a new row is inserted. Already-synced
versions are skipped (ON CONFLICT DO NOTHING semantics).
"""

import hashlib
import os
import sys

# Run from the backend/ directory so imports resolve correctly.
BACKEND_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "backend")
BACKEND_DIR = os.path.abspath(BACKEND_DIR)
sys.path.insert(0, BACKEND_DIR)

from dotenv import load_dotenv
load_dotenv(os.path.join(BACKEND_DIR, ".env"))

from db import SessionLocal
from db.models import PromptVersion

PROMPTS_DIR = os.path.join(BACKEND_DIR, "prompts")


def _hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def sync():
    if not os.path.isdir(PROMPTS_DIR):
        print(f"ERROR: prompts directory not found: {PROMPTS_DIR}")
        sys.exit(1)

    files = sorted(f for f in os.listdir(PROMPTS_DIR) if f.endswith(".txt"))
    if not files:
        print("No .txt prompt files found.")
        return

    db = SessionLocal()
    inserted = 0
    skipped = 0
    try:
        for fname in files:
            name = os.path.splitext(fname)[0]
            path = os.path.join(PROMPTS_DIR, fname)
            with open(path, encoding="utf-8") as fh:
                content = fh.read()
            h = _hash(content)

            exists = db.query(PromptVersion).filter(
                PromptVersion.name == name,
                PromptVersion.hash == h,
            ).first()

            if exists:
                skipped += 1
                print(f"  skip  {name}  #{h[:8]}")
            else:
                db.add(PromptVersion(name=name, hash=h, content=content))
                inserted += 1
                print(f"  sync  {name}  #{h[:8]}")

        db.commit()
    except Exception as exc:
        db.rollback()
        print(f"ERROR: {exc}")
        sys.exit(1)
    finally:
        db.close()

    print(f"\nDone — {inserted} inserted, {skipped} already up-to-date.")


if __name__ == "__main__":
    sync()
