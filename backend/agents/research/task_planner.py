from __future__ import annotations

import json
import logging
import re

from langchain_core.messages import HumanMessage
from langfuse import observe
from pydantic import BaseModel, Field

from observability import ainvoke_traced_generation
from .runtime_prompts import research_node_system_messages
from .state import default_summary_coverage
from rag.store import _lang_from_text

logger = logging.getLogger(__name__)


class CoverageItemModel(BaseModel):
    id: str = Field(description="Stable snake_case id for this coverage item.")
    label: str = Field(default="", description="Short Traditional Chinese display label for this item.")
    description: str = Field(description="What evidence this item needs.")
    required: bool = Field(description="Whether this item is required before final answer.")
    use_hyde: bool = Field(
        default=False,
        description=(
            "Set true for interpretive or derived slots that need hypothetical passage generation — "
            "e.g. 'interesting points', 'learning hooks', '有趣發現', '導讀切入'. "
            "Keep false for factual slots that can be found directly in document sections."
        ),
    )
    search_hints: list[str] = Field(description="Concrete search terms or section names.")
    success_criteria: str = Field(description="How to know this item is sufficiently answered.")


class ResearchPlan(BaseModel):
    goal: str = Field(description="The user-facing goal for this request.")
    coverage_items: list[CoverageItemModel] = Field(description="Evidence coverage items for this request.")
    output_contract: str = Field(description="How the final answer should be shaped.")

    def as_state_parts(self) -> tuple[str, list[dict], str]:
        return (
            self.goal,
            [item.model_dump() for item in self.coverage_items],
            self.output_contract,
        )


def _item(
    item_id: str,
    label: str,
    description: str,
    hints: list[str],
    criteria: str,
    *,
    required: bool = True,
    use_hyde: bool = False,
) -> CoverageItemModel:
    return CoverageItemModel(
        id=item_id,
        label=label,
        description=description,
        required=required,
        use_hyde=use_hyde,
        search_hints=hints,
        success_criteria=criteria,
    )


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(term.lower() in lowered for term in terms)


def _normalise_item_id(raw: str, used: set[str]) -> str:
    value = re.sub(r"[^A-Za-z0-9_]+", "_", raw.strip().lower()).strip("_")
    value = re.sub(r"_+", "_", value)
    if not value:
        value = "coverage_item"
    if value[0].isdigit():
        value = f"item_{value}"
    base = value[:48].strip("_") or "coverage_item"
    value = base
    index = 2
    while value in used:
        suffix = f"_{index}"
        value = f"{base[:48 - len(suffix)]}{suffix}"
        index += 1
    used.add(value)
    return value


def _fallback_label(item_id: str, description: str) -> str:
    labels = {
        "research_motivation": "研究動機",
        "research_methods": "研究方法",
        "research_findings": "研究成果",
        "research_limitations": "研究限制",
        "motivation": "研究動機",
        "method": "研究方法",
        "methods": "研究方法",
        "results": "研究成果",
        "findings": "研究成果",
        "limitations": "研究限制",
        "comparison_scope": "比較範圍",
        "comparison_dimensions": "比較面向",
        "key_differences": "主要差異",
        "answer_evidence": "回答證據",
    }
    if item_id in labels:
        return labels[item_id]
    cleaned = re.sub(r"[\s:：,，。；;、]+", "", description.strip())
    return cleaned[:8] or item_id.replace("_", " ")[:16]


def _question_demands_item(question: str, item: CoverageItemModel, item_id: str, label: str) -> bool:
    q = question or ""
    text = " ".join([
        item_id,
        label,
        item.description,
        item.success_criteria,
        " ".join(item.search_hints),
    ]).lower()

    explicit_pairs = [
        (("限制", "侷限", "局限", "未解決", "不足", "適用範圍"), ("limit", "限制", "侷限", "局限", "未解決", "不足")),
        (("代表性例子", "代表例子", "具體例子", "例子", "案例"), ("example", "evidence", "例子", "案例", "代表")),
        (("有趣", "辨識度", "導讀", "興趣量表"), ("notable", "distinctive", "learning", "hook", "有趣", "辨識", "導讀", "興趣", "特色")),
    ]
    for question_terms, item_terms in explicit_pairs:
        if any(term in q for term in question_terms) and any(term in text for term in item_terms):
            return True
    return False


def _clean_plan(plan: ResearchPlan, task_context: str, question: str) -> ResearchPlan:
    used: set[str] = set()
    items: list[CoverageItemModel] = []
    for item in plan.coverage_items:
        item_id = _normalise_item_id(item.id, used)
        description = item.description.strip()
        if not item_id or not description:
            continue
        label = item.label.strip() or _fallback_label(item_id, description)
        hints = [str(h).strip() for h in item.search_hints if str(h).strip()]
        required = item.required or _question_demands_item(question, item, item_id, label)
        items.append(
            item.model_copy(
                update={
                    "id": item_id,
                    "label": label[:12],
                    "description": description,
                    "required": required,
                    "search_hints": hints[:8],
                    "success_criteria": item.success_criteria.strip(),
                }
            )
        )
        if len(items) >= 6:
            break
    if not items:
        return fallback_research_plan(task_context, question)
    return plan.model_copy(update={"coverage_items": items})


def _comparison_plan(question: str) -> ResearchPlan:
    return ResearchPlan(
        goal=question or "比較文件中的重點差異。",
        coverage_items=[
            _item("comparison_scope", "比較範圍", "確認使用者要比較的對象、文件範圍與問題邊界。", ["比較", "範圍", "對象"], "能明確指出比較對象與資料範圍。"),
            _item("comparison_dimensions", "比較面向", "找出可以比較的具體面向，例如動機、方法、成果、限制或分類。", ["面向", "方法", "成果", "限制"], "能列出有證據支持的比較面向。"),
            _item("key_differences", "主要差異", "整理各比較對象最主要的不同點與代表性證據。", ["差異", "不同", "特色"], "能用文件證據說明主要差異。"),
            _item("evidence_limits", "證據限制", "指出比較時缺乏證據或文件沒有明示的部分。", ["限制", "未明示", "不足"], "能標示比較證據的不足。", required=False),
        ],
        output_contract="以比較表或分段方式回答，保留比較面向、主要差異與證據限制。",
    )


def _student_plan(question: str) -> ResearchPlan:
    return ResearchPlan(
        goal=question or "整理可供高中生導讀或興趣量表使用的摘要。",
        coverage_items=[
            _item("student_friendly_topic", "主題導讀", "整理研究主題、背景與高中生容易理解的切入點。", ["主題", "背景", "研究動機"], "能說明研究在探討什麼。"),
            _item("core_methods", "核心方法", "整理研究如何做，包括資料、步驟、分類與方法名稱。", ["研究方法", "步驟", "分類"], "能轉換成清楚的方法導讀。"),
            _item("distinctive_findings", "特色發現", "找出有趣、有辨識度或可引發興趣的研究成果。", ["研究成果", "發現", "有趣", "特色"], "能保留具體例子與辨識度高的發現。", use_hyde=True),
            _item("learning_hooks", "興趣切入", "整理可轉成興趣量表題目的概念、情境或問題。", ["興趣", "問題", "應用", "例子"], "能產生後續導讀或量表素材。", use_hyde=True),
            _item("limitations", "研究限制", "標示文件明示限制或證據不足處。", ["限制", "不足", "未明示"], "能說明哪些部分不可自行延伸。", required=False),
        ],
        output_contract="以高中生可理解的語氣整理，但所有內容都必須有文件證據。",
    )


def _method_plan(question: str) -> ResearchPlan:
    return ResearchPlan(
        goal=question or "整理文件中的研究方法。",
        coverage_items=[
            _item("method_overview", "方法概述", "找出研究採用的方法名稱與整體設計。", ["研究方法", "方法名稱", "設計"], "能列出核心方法。"),
            _item("data_or_materials", "資料材料", "找出研究使用的文本、資料來源、版本或研究範圍。", ["資料", "文本", "版本", "範圍"], "能說明分析材料。"),
            _item("procedure_steps", "操作步驟", "整理研究實際執行步驟、分類方式與分析流程。", ["研究步驟", "分類", "分析", "流程"], "能重建方法流程。"),
            _item("method_limits", "方法限制", "標示方法、資料或操作上的限制。", ["限制", "不足", "未明示"], "能指出方法邊界。", required=False),
        ],
        output_contract="聚焦研究方法、資料範圍與操作步驟，不補充文件外方法。",
    )


def _result_plan(question: str) -> ResearchPlan:
    return ResearchPlan(
        goal=question or "整理文件中的研究成果。",
        coverage_items=[
            _item("main_findings", "主要發現", "整理作者提出的主要發現與結論。", ["研究成果", "主要發現", "結論"], "能列出核心成果。"),
            _item("specific_evidence", "具體例子", "找出支撐成果的分類、案例、表格或代表性例子。", ["例子", "分類", "表", "代表"], "能以證據支撐成果。"),
            _item("contribution_or_meaning", "成果意義", "整理成果對研究問題、文化理解或應用的意義。", ["意義", "貢獻", "理解", "規則"], "能說明成果價值。"),
            _item("result_limits", "成果限制", "標示成果部分仍未明示或證據不足之處。", ["限制", "不足", "未明示"], "能指出成果邊界。", required=False),
        ],
        output_contract="聚焦研究成果、具體證據與成果意義；無證據處需明確標示。",
    )


def _summary_plan(question: str) -> ResearchPlan:
    return ResearchPlan(
        goal=question or "分析文件並整理研究摘要。",
        coverage_items=[CoverageItemModel(**item) for item in default_summary_coverage()],
        output_contract="提供結構化摘要，保留研究動機、研究方法、研究成果與研究限制；只根據文件證據回答。",
    )


def _summary_fallback_from_question(question: str) -> ResearchPlan:
    q = question or ""
    if _contains_any(q, ("比較", "差異", "異同", "對照")):
        return _comparison_plan(q)
    if _contains_any(q, ("高中生", "導讀", "興趣量表", "學生", "學習")):
        return _student_plan(q)
    method_terms = ("方法", "步驟", "流程", "分析方法")
    if _contains_any(q, method_terms) and not _contains_any(q, ("動機", "成果", "限制")):
        return _method_plan(q)
    result_terms = ("成果", "發現", "結論", "貢獻")
    if _contains_any(q, result_terms) and not _contains_any(q, ("動機", "方法", "限制")):
        return _result_plan(q)
    return _summary_plan(q)


def _count_documents(document_context: str) -> int:
    import re as _re
    return len(_re.findall(r"^\[.+?\]", document_context, flags=_re.MULTILINE))


def fallback_research_plan(task_context: str, question: str, document_count: int = 1) -> ResearchPlan:
    task_context = (task_context or "research").lower()
    if task_context == "retrieval":
        return ResearchPlan(
            goal=question or "回答文件相關問題。",
            coverage_items=[
                _item("answer_evidence", "回答證據", "找到可直接回答使用者問題的文件證據。", ["答案", "內容", "結論", "證據"], "能用具體證據回答問題。"),
                _item("source_context", "來源脈絡", "補充證據所在章節、頁面或上下文。", ["來源", "限制", "脈絡"], "能說明證據背景。", required=False),
            ],
            output_contract="根據文件證據直接回答；沒有證據時說明文件未明示。",
        )
    # Multiple documents without explicit single-doc analysis request → comparison plan
    q = question or ""
    if document_count > 1 and not _contains_any(q, ("高中生", "導讀", "興趣量表", "摘要這篇", "分析這篇")):
        return _comparison_plan(q)
    return _summary_fallback_from_question(question)


@observe(as_type="agent", name="create_research_plan")
async def create_research_plan(
    llm,
    *,
    question: str,
    task_context: str,
    document_context: str,
) -> ResearchPlan:
    doc_count = _count_documents(document_context)
    multi_doc_hint = (
        f"\n\n注意：本次有 {doc_count} 份文件，請考慮生成比較型 coverage items（比較範圍、比較面向、主要差異）。"
        if doc_count > 1 else ""
    )
    doc_language = _lang_from_text(document_context)
    lang_hint = (
        "\n\n【語言】此文件為英文，每個 item 的 search_hints 必須全部使用英文術語，不可翻譯成中文。label 仍使用繁體中文。"
        if doc_language == "en" else ""
    )
    planner = llm.with_structured_output(ResearchPlan, strict=True)
    try:
        messages = [
            *research_node_system_messages("task_planner"),
            HumanMessage(
                content=json.dumps(
                    {
                        "question": question,
                        "task_context": task_context,
                        "document_context": document_context + multi_doc_hint + lang_hint,
                        "instruction": "請規劃 coverage items，每個 item 描述一個證據需求，使用繁體中文 label。",
                    },
                    ensure_ascii=False,
                )
            ),
        ]
        from .runtime_prompts import research_node_stack_metadata

        plan: ResearchPlan = await ainvoke_traced_generation(
            planner,
            messages,
            prompt_name="task_planner",
            metadata={
                "agent_name": "research",
                **research_node_stack_metadata("task_planner"),
            },
        )
        return _clean_plan(plan, task_context, question)
    except Exception as exc:
        logger.warning("create_research_plan failed: %s", exc)
        return fallback_research_plan(task_context, question, document_count=doc_count)
