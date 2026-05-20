"""Extraction Pipeline Step 4 quality scoring.

This is not a general agent. It is the fixed QC step for structured document
summary extraction, powered by the ``summary_quality`` prompt.
"""

import json
import logging

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import AzureChatOpenAI
from langfuse import observe
from pydantic import BaseModel, Field

from config import settings
from observability import ainvoke_traced_generation, update_current_observation_io
from prompting.loader import load_stack

logger = logging.getLogger(__name__)

PROMPT_NAME = "summary_quality"
STACK_NAME = "extract_step4"


class QualityScoreDetail(BaseModel):
    motivation_clarity: float = Field(ge=0, le=5, description="研究動機的清晰程度與具體性")
    method_specificity: float = Field(ge=0, le=5, description="研究方法的具體程度，有無方法名稱與操作細節")
    results_concreteness: float = Field(ge=0, le=5, description="研究成果是否有具體分類、數字、代表性例子或明確結論")
    limitations_honesty: float = Field(
        ge=0, le=5,
        description=(
            "研究限制的誠實程度：是否明示原文提到的限制，"
            "或誠實標示文件未明示。不可自行補充推測性限制。"
        ),
    )
    overall: float = Field(ge=0, le=5, description="四維度加權分數")
    issues: list[str] = Field(default_factory=list, description="具體問題清單（最多 4 條）")


def _llm():
    return AzureChatOpenAI(
        azure_deployment=settings.azure_mini_deployment or settings.azure_chat_deployment,
        azure_endpoint=settings.azure_openai_endpoint,
        api_key=settings.azure_openai_api_key.get_secret_value(),
        api_version=settings.azure_openai_api_version,
        temperature=0,
    )


def _weighted_overall(detail: QualityScoreDetail) -> float:
    """motivation*0.25 + method*0.30 + results*0.25 + limitations*0.20"""
    raw = (
        detail.motivation_clarity * 0.25
        + detail.method_specificity * 0.30
        + detail.results_concreteness * 0.25
        + detail.limitations_honesty * 0.20
    )
    return max(0.0, min(5.0, round(raw, 2)))


@observe(as_type="chain", name="Check Summary Quality")
async def score_extraction(
    summary_dict: dict,
    abstract_text: str | None = None,
) -> tuple[float, str]:
    """評估結構化萃取品質。返回 (overall_score 0-5, explanation)。

    加權：motivation*0.25 + method*0.30 + results*0.25 + limitations*0.20
    """
    stack = load_stack(STACK_NAME)
    system_messages = [
        SystemMessage(content=content)
        for content in stack.contents
    ]
    payload = {
        "summary": summary_dict,
        "abstract_text": (abstract_text or "")[:2000],
        "instruction": (
            "請按照評分維度對此摘要打分。"
            "若有提供 abstract_text，請比對摘要聲明是否與原文一致，"
            "無法驗證的聲明請列入 issues。"
        ),
    }
    update_current_observation_io(
        input={
            "motivation_len": len(summary_dict.get("motivation", "")),
            "method_len": len(summary_dict.get("method", "")),
            "results_len": len(summary_dict.get("results", "")),
            "has_abstract": bool(abstract_text),
        },
    )
    try:
        scorer = _llm().with_structured_output(QualityScoreDetail)
        result: QualityScoreDetail = await ainvoke_traced_generation(
            scorer,
            [
            *system_messages,
            HumanMessage(content=json.dumps(payload, ensure_ascii=False)),
            ],
            prompt_name=PROMPT_NAME,
            metadata={
                "task_type": "document_extraction",
                "agent_name": "summary_quality",
                **stack.metadata(),
            },
        )
        overall = _weighted_overall(result)
        issues_text = "；".join(result.issues[:4]) if result.issues else "無明顯問題"
        explanation = (
            f"動機 {result.motivation_clarity:.1f} / "
            f"方法 {result.method_specificity:.1f} / "
            f"成果 {result.results_concreteness:.1f} / "
            f"限制 {result.limitations_honesty:.1f} | {issues_text}"
        )
        update_current_observation_io(
            output={
                "overall": overall,
                "motivation_clarity": result.motivation_clarity,
                "method_specificity": result.method_specificity,
                "results_concreteness": result.results_concreteness,
                "limitations_honesty": result.limitations_honesty,
                "issues": result.issues,
            },
        )
        return overall, explanation
    except Exception as exc:
        logger.warning("Quality agent failed: %s", exc)
        return 0.0, f"quality_check_failed: {exc}"


async def score_extraction_detail(
    summary_dict: dict,
    abstract_text: str | None = None,
) -> QualityScoreDetail | None:
    """返回完整多維評分物件（供需要細節的呼叫者使用）。"""
    stack = load_stack(STACK_NAME)
    system_messages = [
        SystemMessage(content=content)
        for content in stack.contents
    ]
    payload = {
        "summary": summary_dict,
        "abstract_text": (abstract_text or "")[:2000],
        "instruction": "請按照四個維度評分並列出問題。",
    }
    try:
        scorer = _llm().with_structured_output(QualityScoreDetail)
        result: QualityScoreDetail = await ainvoke_traced_generation(
            scorer,
            [
            *system_messages,
            HumanMessage(content=json.dumps(payload, ensure_ascii=False)),
            ],
            prompt_name=PROMPT_NAME,
            metadata={
                "task_type": "document_extraction",
                "agent_name": "summary_quality",
                **stack.metadata(),
            },
        )
        result = result.model_copy(update={"overall": _weighted_overall(result)})
        return result
    except Exception as exc:
        logger.warning("Quality agent detail failed: %s", exc)
        return None
