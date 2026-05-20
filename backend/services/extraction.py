from utils import new_id
"""Extract structured summaries: agent handles RAG, then a second LLM call structures the output."""
import asyncio
import uuid
import logging
import os
from typing import Annotated, Callable
from pydantic import BaseModel, Field
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import AzureChatOpenAI

from config import settings
from agents.research import run_research_summary, trace_metadata as summary_trace_metadata
from observability import ainvoke_traced_generation, update_current_observation_io
from prompting.loader import load_stack
from langfuse import observe, propagate_attributes

logger = logging.getLogger(__name__)


async def _emit_stage(on_stage, msg: str) -> None:
    if not on_stage:
        return
    try:
        result = on_stage(msg)
        if asyncio.iscoroutine(result):
            await result
    except Exception:
        pass


def _extraction_thread_id(document_id: int) -> str:
    return f"document-extraction:{document_id}"


def _stack_system_messages(stack_name: str) -> list[SystemMessage]:
    return [SystemMessage(content=content) for content in load_stack(stack_name).contents]


def _generation_metadata(stack_name: str, task_type: str, agent_name: str) -> dict:
    return {
        "task_type": task_type,
        "route_intent": None,
        "agent_name": agent_name,
        **load_stack(stack_name).metadata(),
    }


def _prefixed_stack_metadata(stack_name: str, prefix: str) -> dict:
    meta = load_stack(stack_name).metadata()
    return {
        f"{prefix}_prompt_stack_json": meta.get("prompt_stack_json"),
        f"{prefix}_prompt_stack_tokens": meta.get("prompt_stack_tokens"),
    }

# ── Step 2 model: core academic summary ──────────────────────────────────────
class CoreExtractionResponse(BaseModel):
    motivation: str = Field(description="研究動機與背景，上限 200 字，繁體中文。")
    method: str = Field(description="研究方法與步驟，上限 200 字，繁體中文。多個並列步驟時可用條列，否則用段落。")
    results: str = Field(description="研究成果與發現，上限 200 字，繁體中文。多項具體成果時可用條列，否則用段落。")
    tags: list[str] = Field(description="3 至 5 個關鍵標籤，繁體中文為主。")


# ── Step 3 model: interest survey (reads Step 1 raw summary) ─────────────────
InterestQuestion = Annotated[str, Field(min_length=45, max_length=80)]


class QuestionGenerationResponse(BaseModel):
    intro: str = Field(
        min_length=100,
        max_length=220,
        description="100~180字導讀文字。讓高中生感受這個研究世界為什麼迷人，而不是介紹研究目的。"
    )
    questions: list[InterestQuestion] = Field(
        min_length=3,
        max_length=3,
        description=(
            "三題興趣量表題目，每題45~80字，分別對應情境吸引力、研究方式吸引力、思考方式吸引力三種題型。"
            "每題設計上必須能讓學生用 1~5 分回答個人興趣程度，但題目本身不要寫出評分說明，由系統另行顯示。"
            "不是考研究內容。不可三題都用「你是否對」開頭。"
        )
    )


_llm_base = AzureChatOpenAI(
    azure_deployment=settings.azure_chat_deployment,
    azure_endpoint=settings.azure_openai_endpoint,
    api_key=settings.azure_openai_api_key.get_secret_value(),
    api_version=settings.azure_openai_api_version,
    temperature=0,
)

# Step 2: structure raw RAG output → motivation / method / results / tags
_structure_llm = _llm_base.with_structured_output(CoreExtractionResponse)

# Step 3: generate intro + questions from Step 1 raw summary
_question_llm = _llm_base.with_structured_output(QuestionGenerationResponse)


def _get_document_abstract(document_id: int) -> str | None:
    from db import Document, db_session

    with db_session() as db:
        row = db.query(Document.abstract_text).filter(Document.id == document_id).first()
        return row[0] if row and row[0] else None


def _save_quality_score(observation_id: str, score: float, note: str) -> None:
    from db import db_session, Trace

    with db_session() as db:
        trace = db.query(Trace).filter(Trace.observation_id == observation_id).first()
        if trace:
            trace.quality_score = score
            trace.user_feedback = note[:500]
            db.commit()


STEP1_RESEARCH_QUESTION = (
    "請根據系統指示分析這份文件，整理研究動機、研究方法、研究成果與研究限制。"
    "請根據文件證據回答，不要自行補充。"
    "請保留具體分類、研究步驟、方法名稱、代表性例子與限制說明。"
)


def _empty_summary() -> dict:
    return {"motivation": "資料不足", "method": "資料不足", "results": "資料不足", "tags": []}


@observe(as_type="agent", name="Research Evidence Collection")
async def run_document_research_step1(
    document_id: int,
    on_stage: Callable[[str], None] | None = None,
) -> tuple[str, list[str], str]:
    """Run the expensive research graph only and return (raw_answer, sources, observation_id)."""
    observation_id = new_id()
    thread_id = _extraction_thread_id(document_id)
    logger.info("Step 1: agent RAG for document %d (observation_id=%s)", document_id, observation_id)
    await _emit_stage(on_stage, "Step 1 研究檢索中")
    trace_meta = summary_trace_metadata(thread_id, [document_id], stack_name="research_runtime")
    trace_meta.update({
        "document_id": str(document_id),
        "type": "extraction",
        "agent_name": "research_agent",
        **_prefixed_stack_metadata("extract_step2", "extract_step2"),
        **_prefixed_stack_metadata("extract_step3", "extract_step3"),
        **_prefixed_stack_metadata("extract_step4", "extract_step4"),
    })
    summary_result = await run_research_summary(
        question=STEP1_RESEARCH_QUESTION,
        thread_id=thread_id,
        document_ids=[document_id],
        metadata=trace_meta,
        observation_id=observation_id,
        on_stage=on_stage,
        max_searches=10,
        max_consecutive_no_new=2,
    )
    return summary_result.response, summary_result.sources, observation_id


@observe(as_type="chain", name="Structure Summary Fields")
async def structure_research_step2(
    answer: str,
    on_stage: Callable[[str], None] | None = None,
) -> dict:
    """Structure Step 1 raw research into motivation/method/results/tags."""
    await _emit_stage(on_stage, "整理結構中")
    structure_messages = _stack_system_messages("extract_step2")
    messages = [
        *structure_messages,
        HumanMessage(content=answer),
    ]
    core: CoreExtractionResponse = await ainvoke_traced_generation(
        _structure_llm,
        messages,
        prompt_name="summary_structure",
        metadata=_generation_metadata("extract_step2", "document_extraction", "summary_structure"),
    )
    return core.model_dump()


@observe(as_type="chain", name="Generate Student Guidance")
async def generate_interest_step3(
    answer: str,
    on_stage: Callable[[str], None] | None = None,
) -> dict:
    """Generate student intro and interest questions from Step 1 raw research."""
    await _emit_stage(on_stage, "生成導讀與問題")
    question_messages = _stack_system_messages("extract_step3")
    messages = [
        *question_messages,
        HumanMessage(
            content=(
            "以下資料用於產生高中生導讀 intro 與 3 題興趣量表 questions。\n"
            "請只依據下面的高資訊量研究摘要。\n"
            "不要重新摘要，不要出考題，不要三題都用「你是否對...」開頭。\n\n"
            "【研究摘要】\n"
            f"{answer}"
            )
        ),
    ]
    questions_resp: QuestionGenerationResponse = await ainvoke_traced_generation(
        _question_llm,
        messages,
        prompt_name="question_generator",
        metadata=_generation_metadata("extract_step3", "document_extraction", "question_generator"),
    )
    return questions_resp.model_dump()


@observe(as_type="chain", name="Document Summary Extraction")
async def extract_document_summary_with_raw(
    document_id: int,
    on_stage: Callable[[str], None] | None = None,
) -> tuple[dict, str, str, list[str]]:
    """Step 1: agent does full ReAct RAG. Step 2: structure the free-text answer into Pydantic.
    Returns (summary_dict, observation_id, raw_answer, raw_sources)."""

    with propagate_attributes(session_id=_extraction_thread_id(document_id)):
        answer, _sources, observation_id = await run_document_research_step1(document_id, on_stage=on_stage)

        if not answer or answer.strip() == "Unable to generate a response.":
            return _empty_summary(), observation_id, answer, _sources

        logger.info("Step 2+3: structuring and question generation in parallel for document %d", document_id)
        core_result, question_result = await asyncio.gather(
            structure_research_step2(answer),
            generate_interest_step3(answer),
        )
        await _emit_stage(on_stage, "結構化與導讀題目生成完成")
        result = {**core_result, **question_result}

        if os.getenv("ENABLE_QUALITY_CHECK", "false").lower() == "true":
            result, observation_id, answer, _sources = await _run_quality_check(
                result=result,
                observation_id=observation_id,
                answer=answer,
                sources=_sources,
                document_id=document_id,
                on_stage=on_stage,
            )
        return result, observation_id, answer, _sources


@observe(as_type="chain", name="Step 4 Quality Check")
async def _run_quality_check(
    *,
    result: dict,
    observation_id: str,
    answer: str,
    sources: list[str],
    document_id: int,
    on_stage: Callable[[str], None] | None,
) -> tuple[dict, str, str, list[str]]:
    """Run quality scoring and optionally retry Step 1 if score is below threshold."""
    from services.extraction_quality import score_extraction

    logger.info("Step 4: quality check for document %d", document_id)
    await _emit_stage(on_stage, "品質檢查")

    abstract_text = await asyncio.to_thread(_get_document_abstract, document_id)
    score, note = await score_extraction(result, abstract_text)

    update_current_observation_io(
        input={"document_id": document_id, "observation_id": observation_id},
        output={"quality_score": score, "quality_note": note, "retried": False},
    )

    # Quality gate: retry once if score is below threshold
    if score < 2.5:
        logger.warning("Quality score %.1f below threshold for doc %d; retrying step 1", score, document_id)
        await _emit_stage(on_stage, "品質未達標，重新搜尋")
        answer2, sources2, observation_id2 = await run_document_research_step1(document_id, on_stage=on_stage)
        if answer2 and answer2.strip() != "Unable to generate a response.":
            core2, question2 = await asyncio.gather(
                structure_research_step2(answer2),
                generate_interest_step3(answer2),
            )
            result2 = {**core2, **question2}
            score2, note2 = await score_extraction(result2, abstract_text)
            if score2 > score:
                logger.info("Retry improved quality %.1f → %.1f for doc %d", score, score2, document_id)
                result, score, note = result2, score2, note2
                answer, sources, observation_id = answer2, sources2, observation_id2
                update_current_observation_io(
                    output={"quality_score": score, "quality_note": note, "retried": True},
                )

    result["quality_score"] = score
    result["quality_note"] = note
    await asyncio.to_thread(_save_quality_score, observation_id, score, note)
    return result, observation_id, answer, sources
