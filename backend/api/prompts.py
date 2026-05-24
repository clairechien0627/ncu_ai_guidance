import os

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from db import get_db
from db.models import Observation, PromptVersion, Score

from api.dependencies import require_admin
router = APIRouter(dependencies=[Depends(require_admin)])

PROMPTS_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "prompts")
)


def _local_prompts() -> list[str]:
    if not os.path.isdir(PROMPTS_DIR):
        return []
    return sorted(
        os.path.splitext(f)[0]
        for f in os.listdir(PROMPTS_DIR)
        if f.endswith(".txt")
    )


def _word_count(content: str) -> int:
    return len(content.split())


@router.get("/api/prompts")
def list_prompts(db: Session = Depends(get_db)):
    """List all prompts with their latest version and quality stats."""
    local_names = _local_prompts()

    # Latest version per prompt from DB
    latest_subq = (
        db.query(
            PromptVersion.name,
            func.max(PromptVersion.synced_at).label("latest_at"),
        )
        .group_by(PromptVersion.name)
        .subquery()
    )
    latest_versions = (
        db.query(PromptVersion)
        .join(
            latest_subq,
            (PromptVersion.name == latest_subq.c.name)
            & (PromptVersion.synced_at == latest_subq.c.latest_at),
        )
        .all()
    )
    version_map = {v.name: v for v in latest_versions}

    version_counts = dict(
        db.query(PromptVersion.name, func.count(PromptVersion.id))
        .group_by(PromptVersion.name)
        .all()
    )

    quality_map: dict[str, float] = {}
    v2_quality: dict[str, list[float]] = {}
    seen_quality: set[tuple[str, str]] = set()
    for trace_id, prompt_name, score_val in (
        db.query(Observation.trace_id, Observation.prompt_name, Score.value)
        .join(Score, Score.trace_id == Observation.trace_id)
        .filter(Score.name == "overall", Score.value.isnot(None))
        .filter(Observation.prompt_name.isnot(None))
        .all()
    ):
        if not prompt_name or (trace_id, prompt_name) in seen_quality:
            continue
        seen_quality.add((trace_id, prompt_name))
        v2_quality.setdefault(prompt_name, []).append(float(score_val))
    for pname, vals in v2_quality.items():
        quality_map[pname] = round(sum(vals) / len(vals), 2)

    result = []
    for name in local_names:
        v = version_map.get(name)
        result.append({
            "name":            name,
            "current_hash":    v.hash[:8] if v else None,
            "word_count":      _word_count(v.content) if v else None,
            "version_count":   version_counts.get(name, 0),
            "synced_at":       v.synced_at.isoformat() if v else None,
            "avg_quality_score": quality_map.get(name),
        })
    return result


@router.get("/api/prompts/{name}")
def get_prompt_detail(name: str, db: Session = Depends(get_db)):
    """Return current content and full version history for a prompt."""
    versions = (
        db.query(PromptVersion)
        .filter(PromptVersion.name == name)
        .order_by(PromptVersion.synced_at.desc())
        .all()
    )
    if not versions:
        raise HTTPException(status_code=404, detail="Prompt not found or no versions synced")

    latest = versions[0]
    return {
        "name":    name,
        "content": latest.content,
        "versions": [
            {
                "hash":       v.hash[:8],
                "full_hash":  v.hash,
                "synced_at":  v.synced_at.isoformat(),
                "word_count": _word_count(v.content),
            }
            for v in versions
        ],
    }


@router.get("/api/prompts/{name}/versions/{hash_prefix}")
def get_prompt_version(name: str, hash_prefix: str, db: Session = Depends(get_db)):
    """Return the content of a specific prompt version by hash prefix."""
    v = (
        db.query(PromptVersion)
        .filter(
            PromptVersion.name == name,
            PromptVersion.hash.like(f"{hash_prefix}%"),
        )
        .first()
    )
    if not v:
        raise HTTPException(status_code=404, detail="Version not found")
    return {
        "name":       name,
        "hash":       v.hash[:8],
        "full_hash":  v.hash,
        "content":    v.content,
        "synced_at":  v.synced_at.isoformat(),
        "word_count": _word_count(v.content),
    }
