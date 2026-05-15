from __future__ import annotations

import operator
from dataclasses import dataclass, field
from typing import Annotated, Literal, TypedDict



SlotStatus = Literal["FILLED", "PARTIAL", "NOT_FILLED", "EXHAUSTED"]

DEFAULT_SUMMARY_COVERAGE: tuple[str, ...] = (
    "research_motivation",
    "research_methods",
    "research_findings",
    "research_limitations",
)


def _merge_dict_overwrite(a: dict | None, b: dict | None) -> dict:
    return {**(a or {}), **(b or {})}


def _merge_evidence_dict(a: dict | None, b: dict | None) -> dict:
    result = {str(k): list(v or []) for k, v in (a or {}).items()}
    for slot, notes in (b or {}).items():
        key = str(slot)
        existing = {str(item) for item in result.get(key, [])}
        result.setdefault(key, [])
        for note in notes or []:
            if str(note) not in existing:
                result[key].append(note)
                existing.add(str(note))
    return result


_FLAGS_CLEAR: list = ["__clear__"]


def _batch_flags_reducer(a: list | None, b: list | None) -> list:
    if b is _FLAGS_CLEAR:
        return []
    return list(a or []) + list(b or [])


def _keep_last(a, b):
    """Last-write-wins: used for string fields written by parallel slot_workers."""
    return b if b is not None else a


def _merge_unique_list(a: list | None, b: list | None) -> list:
    result = list(a or [])
    seen = {str(item) for item in result}
    for item in b or []:
        if str(item) not in seen:
            result.append(item)
            seen.add(str(item))
    return result

@dataclass
class CoverageItem:
    id: str
    description: str
    label: str = ""
    required: bool = True
    use_hyde: bool = False  # interpretive slots that benefit from HyDE
    search_hints: list[str] = field(default_factory=list)
    success_criteria: str = ""


@dataclass
class SearchStep:
    slot: str
    query: str
    display_intent: str = ""
    keyword_query: str = ""
    semantic_query: str = ""
    section_terms: list[str] = field(default_factory=list)
    use_hyde: bool = False
    quality: str = "UNKNOWN"
    sources: list[str] = field(default_factory=list)
    new_keywords: list[str] = field(default_factory=list)
    note: str = ""
    thought: str = ""
    expected_evidence: str = ""
    planner_rationale: str = ""
    missing_gap: str = ""
    next_search_angle: str = ""


@dataclass
class ResearchState:
    question: str
    document_ids: list[int]
    document_context: str = ""
    task_goal: str = ""
    coverage_items: list[dict] = field(default_factory=list)
    output_contract: str = ""
    search_count: int = 0
    consecutive_no_new: int = 0
    verification_done: bool = False
    known_keywords: list[str] = field(default_factory=list)
    used_queries: list[str] = field(default_factory=list)
    slot_status: dict[str, SlotStatus] = field(default_factory=dict)
    evidence: dict[str, list[str]] = field(default_factory=dict)
    evidence_details: dict[str, list[dict]] = field(default_factory=dict)
    sources: list[str] = field(default_factory=list)
    last_reflection: str = ""
    next_search_angle: str = ""
    suggested_query_terms: list[str] = field(default_factory=list)
    avoid_query_terms: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.coverage_items:
            self.coverage_items = default_summary_coverage()
        ids = self.coverage_ids()
        for item_id in ids:
            self.slot_status.setdefault(item_id, "NOT_FILLED")
            self.evidence.setdefault(item_id, [])
            self.evidence_details.setdefault(item_id, [])
        for stale in list(self.slot_status):
            if stale not in ids:
                self.slot_status.pop(stale, None)
        for stale in list(self.evidence):
            if stale not in ids:
                self.evidence.pop(stale, None)
        for stale in list(self.evidence_details):
            if stale not in ids:
                self.evidence_details.pop(stale, None)

    def coverage_ids(self) -> list[str]:
        # Memoised: coverage_items is stable after __post_init__.
        try:
            return self._coverage_ids_cache  # type: ignore[attr-defined]
        except AttributeError:
            ids: list[str] = []
            for item in self.coverage_items:
                item_id = str(item.get("id", "")).strip()
                if item_id and item_id not in ids:
                    ids.append(item_id)
            result = ids or list(DEFAULT_SUMMARY_COVERAGE)
            object.__setattr__(self, '_coverage_ids_cache', result)
            return result

    def coverage_item(self, item_id: str) -> dict:
        for item in self.coverage_items:
            if item.get("id") == item_id:
                return item
        return {"id": item_id, "label": item_id, "description": item_id, "required": True}

    def coverage_label(self, item_id: str) -> str:
        item = self.coverage_item(item_id)
        label = str(item.get("label") or "").strip()
        if label:
            return label
        labels = {
            "motivation": "研究動機",
            "method": "研究方法",
            "methods": "研究方法",
            "results": "研究成果",
            "findings": "研究成果",
            "limitations": "研究限制",
            "research_motivation": "研究動機",
            "research_methods": "研究方法",
            "research_findings": "研究成果",
            "research_limitations": "研究限制",
        }
        return labels.get(item_id, item_id.replace("_", " "))

    def required_coverage_ids(self) -> list[str]:
        # Memoised: coverage_items is stable after __post_init__.
        try:
            return self._required_coverage_ids_cache  # type: ignore[attr-defined]
        except AttributeError:
            ids = [
                str(item.get("id"))
                for item in self.coverage_items
                if item.get("required", True) and item.get("id")
            ]
            result = ids or self.coverage_ids()
            object.__setattr__(self, '_required_coverage_ids_cache', result)
            return result

    def weakest_slot(self) -> str:
        rank: dict[SlotStatus, int] = {
            "NOT_FILLED": 0,
            "PARTIAL": 1,
            "EXHAUSTED": 2,
            "FILLED": 3,
        }
        ids = self.required_coverage_ids()
        return min(ids, key=lambda slot: rank.get(self.slot_status.get(slot, "NOT_FILLED"), 0))

    def ready_for_verification(self) -> bool:
        required = self.required_coverage_ids()
        return (
            self.search_count >= 2
            and all(self.slot_status.get(slot) in ("FILLED", "PARTIAL", "EXHAUSTED") for slot in required)
            and not self.verification_done
        )

    def done(self, min_evidence_per_slot: int = 0) -> bool:
        required = self.required_coverage_ids()
        if not self.verification_done:
            return False
        for slot in required:
            status = self.slot_status.get(slot, "NOT_FILLED")
            if status not in ("FILLED", "PARTIAL", "EXHAUSTED"):
                return False
            # Document extraction can require evidence notes unless a slot is truly EXHAUSTED.
            if min_evidence_per_slot > 0 and status != "EXHAUSTED":
                if len(self.evidence.get(slot, [])) < min_evidence_per_slot:
                    return False
        return True

    def add_sources(self, sources: list[str]) -> None:
        for source in sources:
            if source not in self.sources:
                self.sources.append(source)

    def add_keywords(self, keywords: list[str]) -> None:
        for keyword in keywords:
            cleaned = keyword.strip()
            if cleaned and cleaned not in self.known_keywords:
                self.known_keywords.append(cleaned)

    def as_prompt_dict(self) -> dict:
        return {
            "task_goal": self.task_goal,
            "output_contract": self.output_contract,
            "coverage_items": self.coverage_items,
            "search_count": self.search_count,
            "known_keywords": self.known_keywords[:30],
            "coverage_status": self.slot_status,
            "slot_status": self.slot_status,
            "used_queries": self.used_queries[-10:],
            "evidence_brief": {
                item_id: notes[-3:] for item_id, notes in self.evidence.items()
            },
            "evidence_details_brief": {
                item_id: notes[-3:] for item_id, notes in self.evidence_details.items()
            },
            "verification_done": self.verification_done,
            "last_reflection": self.last_reflection,
            "next_search_angle": self.next_search_angle,
            "suggested_query_terms": self.suggested_query_terms[-12:],
            "avoid_query_terms": self.avoid_query_terms[-12:],
        }


def default_summary_coverage() -> list[dict]:
    return [
        {
            "id": "research_motivation",
            "label": "研究動機",
            "description": "分析文件中提到的研究背景、研究目的、研究問題，以及作者為何選擇這個主題。",
            "required": True,
            "use_hyde": False,
            "search_hints": ["研究動機", "研究背景", "研究目的", "研究問題", "重要性"],
            "success_criteria": "能清楚說明作者選題原因與此研究的重要性，並指出文件中的具體證據。",
        },
        {
            "id": "research_methods",
            "label": "研究方法",
            "description": "整理作者使用的研究方法、分類方式、分析步驟、材料來源與方法名稱。",
            "required": True,
            "use_hyde": False,
            "search_hints": ["研究方法", "研究步驟", "分類歸納", "分析研究", "對比詮釋"],
            "success_criteria": "能呈現方法名稱、資料範圍、分類標準與具體操作步驟。",
        },
        {
            "id": "research_findings",
            "label": "研究成果",
            "description": "整理作者提出的主要發現、分類結果、規則、通性、代表性例子與有辨識度的結論。",
            "required": True,
            "use_hyde": False,
            "search_hints": ["研究成果", "主要發現", "結論", "小結", "規則", "通性"],
            "success_criteria": "能列出具體發現與代表性例子，並說明這些發現如何回答研究問題。",
        },
        {
            "id": "research_limitations",
            "label": "研究限制",
            "description": "尋找文件明確提到的研究限制、未解問題、資料範圍或方法邊界；若未明示，需標示證據不足。",
            "required": True,
            "use_hyde": False,
            "search_hints": ["研究限制", "未解決問題", "不足", "限制", "適用範圍", "結論"],
            "success_criteria": "能指出文件直接明示的限制；若沒有直接證據，明確說明文件未明示。",
        },
    ]


class ResearchGraphState(TypedDict):
    # ── request config ──────────────────────────────────────────────────────────
    question: str
    document_ids: list[int]
    document_context: str
    run_id: str
    thread_id: str
    metadata: dict
    max_searches: int
    max_searches_per_slot: int
    max_consecutive_no_new: int

    # ── task plan ────────────────────────────────────────────────────────────────
    task_goal: str
    coverage_items: list[dict]
    output_contract: str

    # ── persistent research state ─────────────────────────────────────────────
    search_count: Annotated[int, operator.add]
    consecutive_no_new: int
    verification_done: bool
    known_keywords: Annotated[list[str], _merge_unique_list]
    used_queries: Annotated[list[str], operator.add]
    slot_status: Annotated[dict, _merge_dict_overwrite]
    evidence: Annotated[dict, _merge_evidence_dict]
    evidence_details: Annotated[dict, _merge_evidence_dict]
    sources: Annotated[list[str], _merge_unique_list]
    last_reflection: Annotated[str, _keep_last]
    next_search_angle: Annotated[str, _keep_last]
    suggested_query_terms: Annotated[list[str], _merge_unique_list]
    avoid_query_terms: Annotated[list[str], _merge_unique_list]
    seen_chunk_keys: Annotated[list[str], _merge_unique_list]
    used_query_keys: Annotated[list[str], _merge_unique_list]

    # ── per-run quality constraints ───────────────────────────────────────────
    # Document extraction sets this to 1 so writer only fires when every
    # required slot has at least one real evidence note (or is genuinely EXHAUSTED).
    min_evidence_per_slot: int

    # ── in-flight search (planner → retriever → reflector, cleared by writer) ──
    batch_plan: list[dict]
    _batch_evidence_flags: Annotated[list, _batch_flags_reducer]

    # ── trace & output ────────────────────────────────────────────────────────
    steps_json: Annotated[list, operator.add]
    chunks_by_query_json: Annotated[list, operator.add]
    trace_summary: dict
    messages: Annotated[list, operator.add]
    llm_call_count: Annotated[int, operator.add]
    started_at: str
    final_answer: str
    final_sources: list[str]


class WorkerState(ResearchGraphState):
    worker_slot: str
    worker_hint: str
