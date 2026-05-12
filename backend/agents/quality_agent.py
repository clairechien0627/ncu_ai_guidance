import json
import logging

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import AzureChatOpenAI
from pydantic import BaseModel, Field

from config import settings
from prompting.registry import get as get_prompt

logger = logging.getLogger(__name__)

PROMPT_NAME = "summary_quality"


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
        api_key=settings.azure_openai_api_key,
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


async def score_extraction(
    summary_dict: dict,
    abstract_text: str | None = None,
) -> tuple[float, str]:
    """評估結構化萃取品質。返回 (overall_score 0-5, explanation)。

    加權：motivation*0.25 + method*0.30 + results*0.25 + limitations*0.20
    """
    system = get_prompt(PROMPT_NAME)
    payload = {
        "summary": summary_dict,
        "abstract_text": (abstract_text or "")[:2000],
        "instruction": (
            "請按照評分維度對此摘要打分。"
            "若有提供 abstract_text，請比對摘要聲明是否與原文一致，"
            "無法驗證的聲明請列入 issues。"
        ),
    }
    try:
        scorer = _llm().with_structured_output(QualityScoreDetail)
        result: QualityScoreDetail = await scorer.ainvoke([
            SystemMessage(content=system),
            HumanMessage(content=json.dumps(payload, ensure_ascii=False)),
        ])
        overall = _weighted_overall(result)
        issues_text = "；".join(result.issues[:4]) if result.issues else "無明顯問題"
        explanation = (
            f"動機 {result.motivation_clarity:.1f} / "
            f"方法 {result.method_specificity:.1f} / "
            f"成果 {result.results_concreteness:.1f} / "
            f"限制 {result.limitations_honesty:.1f} | {issues_text}"
        )
        return overall, explanation
    except Exception as exc:
        logger.warning("Quality agent failed: %s", exc)
        return 0.0, f"quality_check_failed: {exc}"


class QualityTraceDetail(BaseModel):
    grounding: float = Field(ge=0, le=5, description="回答中有 chunk 佐證的比例")
    completeness: float = Field(ge=0, le=5, description="覆蓋研究所有必要 slot 的比例（僅 research mode）")
    source_quality: float = Field(ge=0, le=5, description="來源多樣性與不重複頁數")
    format_fit: float = Field(ge=0, le=5, description="格式符合 mode 期望（research 需有結構，chat 可以短）")
    limitations_honesty: float = Field(
        ge=0, le=5,
        description="限制段落不混淆前人批評與本文研究限制；chat/retrieval mode 若無限制段可給 5",
    )
    overall: float = Field(ge=0, le=5, description="加權綜合分數")
    issues: list[str] = Field(default_factory=list, description="具體問題清單（最多 4 條）")


def _trace_weighted_overall(d: QualityTraceDetail) -> float:
    raw = (
        d.grounding * 0.35
        + d.completeness * 0.20
        + d.source_quality * 0.20
        + d.format_fit * 0.15
        + d.limitations_honesty * 0.10
    )
    return max(0.0, min(5.0, round(raw, 2)))


async def score_trace(
    display: dict,
    mode: str,
) -> tuple[float, str, dict]:
    """評估 trace 回應品質（適用 research/chat/retrieval）。

    返回 (overall_score, explanation, detail_dict)。
    對 research mode，completeness 從 trace_summary coverage 直接計算。
    其餘維度由 LLM 評分。
    """
    answer = display.get("answer") or ""
    sources = display.get("sources") or []
    trace_summary = display.get("trace_summary") or {}

    # completeness: count filled/partial slots vs required slots (research only)
    completeness_score = 5.0
    if mode in ("research", "summary") and trace_summary:
        coverage = trace_summary.get("coverage") or []
        required = [c for c in coverage if c.get("required")]
        if required:
            filled = sum(1 for c in required if c.get("status") in ("FILLED", "PARTIAL"))
            completeness_score = round(filled / len(required) * 5, 2)

    system = get_prompt(PROMPT_NAME)
    payload = {
        "mode": mode,
        "answer_preview": answer[:1500],
        "sources_count": len(sources),
        "sources_sample": sources[:5],
        "trace_summary_steps": len(trace_summary.get("steps") or []),
        "instruction": (
            "請評分此 trace 回應的 grounding（引用是否有 chunk 支撐）、"
            "source_quality（來源是否多樣不重複）、"
            f"format_fit（格式是否符合 {mode} mode 期望）、"
            "limitations_honesty（限制段落是否區分本文限制與前人研究的不足，"
            "chat/retrieval mode 若無限制段請給 5）。"
            "completeness 欄位請填入本系統計算值，不用重新評分。"
        ),
    }
    try:
        scorer = _llm().with_structured_output(QualityTraceDetail)
        result: QualityTraceDetail = await scorer.ainvoke([
            SystemMessage(content=system),
            HumanMessage(content=json.dumps(payload, ensure_ascii=False)),
        ])
        result = result.model_copy(update={
            "completeness": completeness_score,
            "overall": _trace_weighted_overall(result.model_copy(update={"completeness": completeness_score})),
        })
        issues_text = "；".join(result.issues[:4]) if result.issues else "無明顯問題"
        explanation = (
            f"接地 {result.grounding:.1f} / "
            f"完整 {result.completeness:.1f} / "
            f"來源 {result.source_quality:.1f} / "
            f"格式 {result.format_fit:.1f} / "
            f"限制 {result.limitations_honesty:.1f} | {issues_text}"
        )
        return result.overall, explanation, result.model_dump()
    except Exception as exc:
        logger.warning("score_trace failed: %s", exc)
        return 0.0, f"score_trace_failed: {exc}", {}


async def score_extraction_detail(
    summary_dict: dict,
    abstract_text: str | None = None,
) -> QualityScoreDetail | None:
    """返回完整多維評分物件（供需要細節的呼叫者使用）。"""
    system = get_prompt(PROMPT_NAME)
    payload = {
        "summary": summary_dict,
        "abstract_text": (abstract_text or "")[:2000],
        "instruction": "請按照四個維度評分並列出問題。",
    }
    try:
        scorer = _llm().with_structured_output(QualityScoreDetail)
        result: QualityScoreDetail = await scorer.ainvoke([
            SystemMessage(content=system),
            HumanMessage(content=json.dumps(payload, ensure_ascii=False)),
        ])
        result = result.model_copy(update={"overall": _weighted_overall(result)})
        return result
    except Exception as exc:
        logger.warning("Quality agent detail failed: %s", exc)
        return None
