from __future__ import annotations

import json
import logging
import re
from typing import Literal

from langchain_core.messages import HumanMessage
from observability import ainvoke_traced_generation
from pydantic import BaseModel, ConfigDict, Field

from .runtime_prompts import research_node_stack_metadata, research_node_system_messages
from .state import ResearchState, SlotStatus

logger = logging.getLogger(__name__)


class EvidenceItem(BaseModel):
    """Structured evidence note. extra='forbid' ensures additionalProperties: false in JSON schema."""
    model_config = ConfigDict(extra="forbid")

    slot_id: str = Field(default="")
    slot_label: str = Field(default="")
    filename: str = Field(default="")
    page: int | None = Field(default=None)
    page_end: int | None = Field(default=None)
    chunk_id: str = Field(default="")
    quote: str = Field(default="")
    interpretation: str = Field(default="")


class CoverageUpdate(BaseModel):
    item_id: str = Field(description="Coverage item id being updated.")
    status: Literal["FILLED", "PARTIAL", "NOT_FILLED"] = Field(description="Updated status.")
    notes: list[str] = Field(description="Short evidence notes from the chunks.")
    evidence: list[EvidenceItem] = Field(default_factory=list, description="Structured evidence notes.")


class Reflection(BaseModel):
    quality: Literal["USEFUL", "NOT_USEFUL", "NO_RESULTS"] = Field(description="Usefulness of current chunks.")
    filled_item: str = Field(description="Main coverage item improved.")
    updates: list[CoverageUpdate] = Field(description="Coverage updates supported by this search.")
    new_keywords: list[str] = Field(description="Useful concrete keywords found in this search.")
    missing_gap: str = Field(description="The most important missing information after this search.")
    next_search_angle: str = Field(description="A natural next search angle based on observed evidence.")
    suggested_query_terms: list[str] = Field(description="Concrete terms that may help the next planner query.")
    avoid_query_terms: list[str] = Field(description="Terms or angles that look exhausted or too generic.")
    rationale: str = Field(description="Short rationale.")


_SLOT_HINTS = {
    "motivation": ("動機", "目的", "背景", "問題", "重要性", "著迷", "為何"),
    "method": ("方法", "步驟", "分類", "歸納", "分析", "對比", "詮釋", "文本"),
    "results": ("成果", "發現", "結論", "小結", "規則", "通性", "意義", "分類", "代表"),
    "limitations": ("限制", "不足", "未解決", "未能", "較少", "困難", "適用範圍"),
    "research_motivation": ("動機", "目的", "背景", "問題", "重要性", "著迷", "為何"),
    "research_methods": ("方法", "步驟", "分類", "歸納", "分析", "對比", "詮釋", "文本"),
    "research_findings": ("成果", "發現", "結論", "小結", "規則", "通性", "意義", "分類", "代表"),
    "research_limitations": ("限制", "不足", "未解決", "未能", "較少", "困難", "適用範圍"),
}


def _extract_keywords(text: str, limit: int = 8) -> list[str]:
    out: list[str] = []
    for token in re.findall(r"《[^》]{1,24}》|〈[^〉]{1,24}〉|[\u4e00-\u9fff]{2,8}|[A-Za-z][A-Za-z0-9_\-]{2,24}", text):
        token = token.strip()
        if token and token not in out:
            out.append(token)
        if len(out) >= limit:
            break
    return out


def _chunk_note(chunk: dict, limit: int = 220) -> str:
    page = chunk.get("page", "?")
    filename = chunk.get("filename", "")
    text = str(chunk.get("content", "")).strip().replace("\n", " ")
    if not text:
        return ""
    prefix = f"[{filename}] p.{page}" if filename else f"p.{page}"
    return f"{prefix}: {text[:limit]}"


def _chunk_evidence(
    chunk: dict,
    *,
    slot_id: str,
    slot_label: str,
    interpretation: str = "",
    limit: int = 260,
) -> dict:
    text = str(chunk.get("content", "")).strip().replace("\n", " ")
    return {
        "slot_id": slot_id,
        "slot_label": slot_label,
        "filename": chunk.get("filename", ""),
        "page": chunk.get("page"),
        "page_end": chunk.get("page_end", chunk.get("page")),
        "chunk_id": chunk.get("id") or chunk.get("chunk_id") or "",
        "quote": text[:limit],
        "interpretation": interpretation or text[:limit],
    }


def _detail_from_note(note: str, *, slot_id: str, slot_label: str) -> dict:
    filename = ""
    page = None
    quote = note
    match = re.match(r"^\[([^\]]+)\]\s+p\.([^:]+):\s*(.*)$", note)
    if match:
        filename = match.group(1)
        try:
            page = int(match.group(2))
        except (ValueError, TypeError):
            page = None
        quote = match.group(3)
    return {
        "slot_id": slot_id,
        "slot_label": slot_label,
        "filename": filename,
        "page": page,
        "page_end": page,
        "chunk_id": "",
        "quote": quote[:260],
        "interpretation": quote[:260],
    }


def _is_limitation_slot(slot: str) -> bool:
    lower = slot.lower()
    return "limit" in lower or "限制" in slot


def _looks_like_prior_work_limitation(text: str) -> bool:
    has_limitation_word = any(term in text for term in ("不足", "未能", "未詳述", "未仔細", "較少", "薄弱", "殊為可惜"))
    has_prior_marker = any(term in text for term in (
        "前人", "學者", "文獻回顧", "近代", "研究者", "其研究", "該文", "該論文",
    )) or bool(re.search(r'[一-鿿]{1,2}氏', text))
    has_this_work_marker = any(term in text for term in (
        "本文", "本研究", "本計畫", "本論文", "筆者", "作者指出本研究",
    ))
    return has_limitation_word and has_prior_marker and not has_this_work_marker


def _filter_limitation_notes(update: CoverageUpdate) -> CoverageUpdate:
    if not _is_limitation_slot(update.item_id):
        return update
    notes = [note for note in update.notes if not _looks_like_prior_work_limitation(note)]
    evidence = [
        item for item in update.evidence
        if not _looks_like_prior_work_limitation(item.quote + item.interpretation)
    ]
    status = update.status
    if update.notes and not notes and status in ("FILLED", "PARTIAL"):
        status = "NOT_FILLED"
    return update.model_copy(update={"notes": notes, "evidence": evidence, "status": status})


def _fallback_reflection(slot: str, chunks: list[dict]) -> Reflection:
    if not chunks:
        return Reflection(
            quality="NO_RESULTS",
            filled_item=slot,
            updates=[CoverageUpdate(item_id=slot, status="NOT_FILLED", notes=[])],
            new_keywords=[],
            missing_gap=f"尚未找到可支撐 {slot} 的有效文件證據。",
            next_search_angle=f"改用章節詞、同義詞或文件既有關鍵詞搜尋 {slot}。",
            suggested_query_terms=[],
            avoid_query_terms=[],
            rationale="本輪沒有取得搜尋片段。",
        )

    useful = [chunk for chunk in chunks if not chunk.get("is_low_quality")]
    slot_label = slot.replace("_", " ")
    notes = [_chunk_note(chunk) for chunk in useful[:2]]
    notes = [note for note in notes if note]
    evidence = [
        _chunk_evidence(chunk, slot_id=slot, slot_label=slot_label)
        for chunk in useful[:2]
        if str(chunk.get("content", "")).strip()
    ]
    text = " ".join(str(chunk.get("content", "")) for chunk in useful[:3])
    keywords = _extract_keywords(text)
    quality = "USEFUL" if notes else "NOT_USEFUL"
    return Reflection(
        quality=quality,
        filled_item=slot,
        updates=[CoverageUpdate(item_id=slot, status="PARTIAL" if notes else "NOT_FILLED", notes=notes, evidence=evidence)],
        new_keywords=keywords,
        missing_gap=f"{slot} 還需要更直接或更完整的文件證據。",
        next_search_angle=" ".join(keywords[:4]) if keywords else f"改用更貼近文件章節的詞搜尋 {slot}。",
        suggested_query_terms=keywords,
        avoid_query_terms=[],
        rationale="fallback reflection：依搜尋片段產生保守證據整理。",
    )


def _slot_match_score(item: dict, slot: str, text: str) -> int:
    label = str(item.get("label") or "")
    description = str(item.get("description") or "")
    hints = [str(h) for h in item.get("search_hints") or []]
    terms = [label, *hints, *_SLOT_HINTS.get(slot, ())]
    score = 0
    for term in terms:
        term = term.strip()
        if term and term in text:
            score += 2 if term == label else 1
    for marker in ("動機", "方法", "成果", "限制"):
        if marker in description and marker in text:
            score += 1
    return score


def _cross_slot_updates(
    state: ResearchState,
    *,
    target_slot: str,
    chunks: list[dict],
    existing_updates: list[CoverageUpdate],
) -> list[CoverageUpdate]:
    """Route one search's evidence to other slots when chunks clearly match them."""
    if not chunks:
        return existing_updates

    by_slot = {update.item_id: update for update in existing_updates}
    combined_text = "\n".join(str(chunk.get("content", "")) for chunk in chunks[:6])
    for item in state.coverage_items:
        slot = str(item.get("id") or "")
        if not slot or slot in by_slot:
            continue
        score = _slot_match_score(item, slot, combined_text)
        if slot != target_slot and score < 3:
            continue
        notes: list[str] = []
        evidence: list[EvidenceItem] = []
        for chunk in chunks[:6]:
            text = str(chunk.get("content", ""))
            if _slot_match_score(item, slot, text) >= 2:
                note = _chunk_note(chunk)
                if note and note not in notes:
                    notes.append(note)
                    evidence.append(_chunk_evidence(
                        chunk,
                        slot_id=slot,
                        slot_label=state.coverage_label(slot),
                        interpretation=note,
                    ))
            if len(notes) >= 2:
                break
        if notes:
            by_slot[slot] = CoverageUpdate(item_id=slot, status="PARTIAL", notes=notes, evidence=evidence)
    return [_filter_limitation_notes(update) for update in by_slot.values()]


async def reflect_results(
    llm,
    *,
    state: ResearchState,
    slot: str,
    query: str,
    chunks: list[dict],
) -> Reflection:
    if not chunks:
        return _fallback_reflection(slot, chunks)

    compact_chunks = [
        {
            "page": chunk.get("page"),
            "section": chunk.get("section"),
            "is_low_quality": chunk.get("is_low_quality"),
            "content": str(chunk.get("content", ""))[:1200],
            "filename": chunk.get("filename", ""),
            "page_end": chunk.get("page_end"),
            "chunk_id": chunk.get("id") or chunk.get("chunk_id") or "",
        }
        for chunk in chunks[:6]
    ]
    try:
        reflector = llm.with_structured_output(Reflection, strict=True)
        messages = [
            *research_node_system_messages("research_reflector"),
            HumanMessage(
                content=json.dumps(
                    {
                        "query": query,
                        "target_slot": slot,
                        "chunks": compact_chunks,
                        "state": state.reflector_prompt_dict(),
                        "instruction": "列出所有被 chunks 直接支撐的 coverage item 更新。",
                    },
                    ensure_ascii=False,
                )
            ),
        ]
        reflection: Reflection = await ainvoke_traced_generation(
            reflector,
            messages,
            prompt_name="research_reflector",
            metadata={
                "task_type": "research_task",
                "agent_name": "research_agent",
                **research_node_stack_metadata("research_reflector"),
            },
        )
    except Exception as exc:
        logger.warning("reflect_results failed (slot=%s, query=%r): %s", slot, query[:60], exc)
        reflection = _fallback_reflection(slot, chunks)

    valid_ids = set(state.coverage_ids())
    updates = [
        update
        for update in reflection.updates
        if update.item_id in valid_ids
        and update.status in ("FILLED", "PARTIAL", "NOT_FILLED")
    ]
    if not updates:
        updates = _fallback_reflection(slot, chunks).updates
    updates = [_filter_limitation_notes(update) for update in updates]
    updates = _cross_slot_updates(state, target_slot=slot, chunks=chunks, existing_updates=updates)
    updates = [update for update in updates if update.notes or update.status == "NOT_FILLED"]
    filled_item = reflection.filled_item if reflection.filled_item in valid_ids else updates[0].item_id
    if reflection.quality == "NOT_USEFUL" and any(update.notes for update in updates):
        reflection = reflection.model_copy(update={"quality": "USEFUL"})
    return reflection.model_copy(update={"updates": updates, "filled_item": filled_item})


def _status_rank(status: SlotStatus) -> int:
    return {
        "NOT_FILLED": 0,
        "PARTIAL": 1,
        "EXHAUSTED": 2,
        "FILLED": 3,
    }.get(status, 0)


def apply_reflection(state: ResearchState, reflection: Reflection) -> bool:
    changed = False

    for update in reflection.updates:
        old = state.slot_status.get(update.item_id, "NOT_FILLED")
        new = update.status
        if _status_rank(new) >= _status_rank(old) and old != new:
            state.slot_status[update.item_id] = new
            changed = True
        for note in update.notes:
            clean = note.strip()
            if clean and clean not in state.evidence.setdefault(update.item_id, []):
                state.evidence[update.item_id].append(clean)
                changed = True
        existing_details = state.evidence_details.setdefault(update.item_id, [])
        details = update.evidence or [
            _detail_from_note(note, slot_id=update.item_id, slot_label=state.coverage_label(update.item_id))
            for note in update.notes
        ]
        for detail in details:
            d = detail.model_dump() if isinstance(detail, EvidenceItem) else (detail if isinstance(detail, dict) else None)
            if d is None:
                continue
            d.setdefault("slot_id", update.item_id)
            d.setdefault("slot_label", state.coverage_label(update.item_id))
            key = (
                d.get("filename"),
                str(d.get("page")),
                d.get("chunk_id"),
                d.get("quote"),
            )
            if key and all((
                existing.get("filename"),
                str(existing.get("page")),
                existing.get("chunk_id"),
                existing.get("quote"),
            ) != key for existing in existing_details):
                existing_details.append(d)
                changed = True

    before = len(state.known_keywords)
    state.add_keywords(reflection.new_keywords)
    changed = changed or len(state.known_keywords) > before

    state.last_reflection = reflection.rationale
    state.next_search_angle = reflection.next_search_angle
    state.suggested_query_terms = [
        term.strip() for term in reflection.suggested_query_terms if term.strip()
    ]
    state.avoid_query_terms = [
        term.strip() for term in reflection.avoid_query_terms if term.strip()
    ]
    return changed
