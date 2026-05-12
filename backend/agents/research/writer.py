from __future__ import annotations

import json
import logging

from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field

from .runtime_prompts import research_node_system_messages
from .state import ResearchState

logger = logging.getLogger(__name__)


class ResearchWriteup(BaseModel):
    answer: str = Field(description="Final answer in Traditional Chinese.")
    sources: list[str] = Field(description='Sources as "filename p.N".')


class AnswerSection(BaseModel):
    coverage_id: str = Field(description="Coverage item id this section answers.")
    title: str = Field(description="Short Traditional Chinese section title.")
    content: str = Field(description="Section content in Traditional Chinese, grounded in evidence.")


class ResearchStructuredWriteup(BaseModel):
    sections: list[AnswerSection] = Field(
        description="Dynamic answer sections. Include one section for each important required coverage item."
    )
    answer: str = Field(description="Complete final answer in Traditional Chinese.")
    sources: list[str] = Field(description='Sources as "filename p.N".')


def _section_title(state: ResearchState, coverage_id: str) -> str:
    return state.coverage_label(coverage_id)


def _format_sections(sections: list[AnswerSection]) -> str:
    blocks: list[str] = []
    for section in sections:
        title = section.title.strip()
        content = section.content.strip()
        if not title or not content:
            continue
        blocks.append(f"{title}：\n{content}")
    return "\n\n".join(blocks)


def _complete_required_sections(
    sections: list[AnswerSection],
    state: ResearchState,
) -> list[AnswerSection]:
    completed = list(sections)
    seen = {section.coverage_id for section in completed}
    for coverage_id in state.required_coverage_ids():
        if coverage_id in seen:
            continue
        completed.append(
            AnswerSection(
                coverage_id=coverage_id,
                title=_section_title(state, coverage_id),
                content="文件中未找到足夠的直接證據；依要求不自行補充，因此標示為證據不足或文件未明示。",
            )
        )
    return completed


def _answer_missing_required_sections(answer: str, state: ResearchState) -> bool:
    if not answer:
        return True
    for coverage_id in state.required_coverage_ids():
        title = _section_title(state, coverage_id)
        if title and title not in answer:
            return True
    return False


def _fallback_writeup(state: ResearchState) -> ResearchWriteup:
    sections: list[AnswerSection] = []
    for item_id in state.coverage_ids():
        details = state.evidence_details.get(item_id, [])
        notes = []
        for detail in details[:4]:
            quote = str(detail.get("quote") or "").strip()
            source = _source_from_detail(detail)
            if quote:
                notes.append(f"{source}: {quote}" if source else quote)
        if not notes:
            notes = [note for note in state.evidence.get(item_id, []) if note.strip()]
        if not notes:
            continue
        sections.append(
            AnswerSection(
                coverage_id=item_id,
                title=_section_title(state, item_id),
                content="\n".join(notes[:4]),
            )
        )

    answer = _format_sections(_complete_required_sections(sections, state))
    return ResearchWriteup(
        answer=answer or "目前沒有找到足夠的文件證據可回答此問題。",
        sources=state.sources[:5],
    )


def _source_from_detail(detail: dict) -> str:
    filename = str(detail.get("filename") or "").strip()
    page = detail.get("page")
    if not filename:
        return ""
    return f"{filename} p.{page}" if page not in (None, "", "?") else filename


def _to_writeup(result: ResearchStructuredWriteup, state: ResearchState) -> ResearchWriteup:
    answer = result.answer.strip()
    sections = _complete_required_sections(result.sections, state)
    section_answer = _format_sections(sections)

    if section_answer and (
        not answer
        or len(answer) < max(160, len(section_answer) // 2)
        or _answer_missing_required_sections(answer, state)
    ):
        answer = section_answer

    sources = result.sources or state.sources[:5]
    return ResearchWriteup(answer=answer, sources=sources)


async def write_summary(llm, state: ResearchState) -> ResearchWriteup:
    writer = llm.with_structured_output(ResearchStructuredWriteup, strict=True)
    structured = {
        "coverage_items": state.coverage_items,
        "coverage_status": state.slot_status,
        "evidence": state.evidence,
        "evidence_details": state.evidence_details,
        "sources": state.sources[:8],
    }
    try:
        result: ResearchStructuredWriteup = await writer.ainvoke(
            [
                *research_node_system_messages("research_writer"),
                HumanMessage(
                    content="\n\n".join([
                        f"問題：{state.question}",
                        f"任務目標：{state.task_goal}",
                        f"輸出規範：{state.output_contract}",
                        f"文件摘要：\n{state.document_context[:2000]}",
                        f"Coverage 狀態與累積證據：\n{json.dumps(structured, ensure_ascii=False)}",
                        "優先使用 evidence_details 中的 filename/page/quote/interpretation。若某個必要項目沒有直接證據，請明確說明，不要自行補充。",
                    ])
                ),
            ]
        )
        writeup = _to_writeup(result, state)
    except Exception as exc:
        logger.warning("write_summary failed: %s", exc)
        writeup = _fallback_writeup(state)

    if not writeup.sources:
        writeup = writeup.model_copy(update={"sources": state.sources[:5]})
    return writeup
