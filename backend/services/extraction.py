"""Extract structured summaries: agent handles RAG, then a second LLM call structures the output."""
import asyncio
import uuid
import logging
import os
from typing import Annotated, Callable
from pydantic import BaseModel, Field
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import AzureChatOpenAI
from langfuse import observe

from config import settings
from agents.research import run_research_summary, trace_metadata as summary_trace_metadata
from observability import ainvoke_traced_generation
from prompting.loader import load_stack
from langfuse import propagate_attributes

logger = logging.getLogger(__name__)


def _extraction_session_id(document_id: int) -> str:
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
    motivation: str = Field(
        description=(
            "目標讀者是高中生，請用他們熟悉的語言說明：這個研究在解決什麼問題？"
            "為什麼這個問題值得關注？它跟我們的日常生活或社會有什麼關係？"
            "請點出這屬於哪個學科領域（例如材料科學、資訊安全、環境化學），"
            "並用一個貼近生活的例子或情境幫助讀者連結（例如「就像手機電池老化一樣……」）。"
            "重要的專業關鍵詞（例如化合物名稱、技術縮寫）必須保留，"
            "但第一次出現時用括號或短句附上白話說明（例如「光催化反應（一種用光分解物質的技術）」）。"
            "【格式】先用 1～2 句話交代背景脈絡，再說明核心問題；"
            "若有 2～3 個並列的背景因素或問題面向，可用列點呈現，讓節奏與研究方法、成果欄位接近。"
            "上限 200 字，精準表達，不為湊字數而堆砌冗言。"
        )
    )
    method: str = Field(
        description=(
            "目標讀者是高中生，請用他們能理解的語言說明：研究者用什麼方式研究？步驟大概是什麼？"
            "若有多個明確步驟或方法，請用 Markdown 條列格式（「- 步驟名稱：說明」），每項單獨一行。"
            "可以用比喻或類比讓步驟更容易想像，但重要術語必須保留並簡單解釋。"
            "上限 200 字，只寫關鍵步驟與工具，不補填不必要的說明。"
        )
    )
    results: str = Field(
        description=(
            "目標讀者是高中生，請用他們能理解的語言說明：研究得到什麼發現或結論？"
            "若有多項具體成果、分類或數據，請用 Markdown 條列格式（「- 項目：說明」），每項單獨一行。"
            "這個成果對實際生活或未來科技有什麼意義？"
            "重要數據或成果指標（例如「準確率達 92%」）必須保留，並說明這個數字代表什麼意思。"
            "若來源文字有提到研究的範圍限制，也請一併說明。"
            "【誠實性要求】若論文在結論、未來建議或討論章節中，"
            "坦承某項技術目標尚未完成、實驗未達預期、硬體限制造成結果不理想，"
            "或某功能被列為「未來工作」，必須在此欄位明確說明這些落差。"
            "上限 200 字，精準為主，不為湊字數而展開。"
        )
    )
    tags: list[str] = Field(
        description=(
            "3至5個幫助高中生快速定位這份研究的標籤，優先從以下三個角度選取："
            "1.學科／應用領域（例如：太陽能材料、網路安全、古典文學、醫療影像辨識）——"
            "  用高中生能認出的領域名稱，避免過於細分的學術次領域；"
            "2.核心技術或方法（例如：機器學習、X光繞射、問卷調查、有機合成）；"
            "3.與日常生活的連結（例如：手機電池、空氣污染、螢幕顯示、食品安全）——"
            "  若研究與某個生活情境相關，加入一個讓人一眼就懂的生活標籤。"
            "標籤請優先使用繁體中文；只有在中文無法準確表達（例如專有縮寫如 MIMO、RAG）時才保留英文原文。"
        )
    )


# ── Step 3 model: interest survey (reads Step 1 raw summary) ─────────────────
InterestQuestion = Annotated[str, Field(min_length=35, max_length=120)]


class QuestionGenerationResponse(BaseModel):
    intro: str = Field(
        min_length=120,
        max_length=260,
        description="120~220字導讀文字。讓高中生快速理解研究方向，並願意作答後面的興趣量表。"
    )
    questions: list[InterestQuestion] = Field(
        min_length=3,
        max_length=3,
        description=(
            "三題興趣量表題目，每題35~120字。順序建議為情境吸引力、研究方式吸引力、思考方式吸引力。"
            "每題都必須能用 1~5 分回答個人興趣程度，不是考研究內容。不可三題都用「你是否對」開頭。"
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


STEP1_RESEARCH_QUESTION = (
    "請根據系統指示分析這份文件，整理研究動機、研究方法、研究成果與限制。"
    "請根據文件證據回答，不要自行補充。"
    "請保留具體分類、研究步驟、方法名稱、代表性例子、限制，以及真正有趣或有辨識度的發現，"
    "讓後續能用這份摘要產生高中生導讀與興趣量表。"
)


def _empty_summary() -> dict:
    return {"motivation": "資料不足", "method": "資料不足", "results": "資料不足", "tags": []}


@observe(as_type="agent", name="Research Evidence Collection")
async def run_document_research_step1(
    document_id: int,
    on_stage: Callable[[str], None] | None = None,
) -> tuple[str, list[str], str]:
    """Run the expensive research graph only and return raw answer, sources, run_id."""
    run_id = str(uuid.uuid4())
    thread_id = _extraction_session_id(document_id)
    logger.info("Step 1: agent RAG for document %d (run_id=%s)", document_id, run_id)
    if on_stage:
        on_stage("Step 1 研究檢索中")
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
        run_id=run_id,
        on_stage=on_stage,
        max_searches=10,
        max_consecutive_no_new=2,
    )
    return summary_result.response, summary_result.sources, run_id


@observe(as_type="chain", name="Structure Summary Fields")
async def structure_research_step2(
    answer: str,
    on_stage: Callable[[str], None] | None = None,
) -> dict:
    """Structure Step 1 raw research into motivation/method/results/tags."""
    if on_stage:
        on_stage("整理結構中")
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
    if on_stage:
        on_stage("生成導讀與問題")
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
    Returns (summary_dict, run_id, raw_answer, raw_sources)."""

    with propagate_attributes(session_id=_extraction_session_id(document_id)):
        answer, _sources, run_id = await run_document_research_step1(document_id, on_stage=on_stage)

        if not answer or answer.strip() == "Unable to generate a response.":
            return _empty_summary(), run_id, answer, _sources

        # Step 2 & 3 — run in parallel; both only read the step1 answer text
        logger.info("Step 2+3: structuring and question generation in parallel for document %d", document_id)
        core_result, question_result = await asyncio.gather(
            structure_research_step2(answer),
            generate_interest_step3(answer),
        )
        if on_stage:
            on_stage("結構化與導讀題目生成完成")
        result = {**core_result, **question_result}

        if os.getenv("ENABLE_QUALITY_CHECK", "false").lower() == "true":
            logger.info("Step 4: quality check for document %d", document_id)
            if on_stage:
                on_stage("品質檢查")
            from services.extraction_quality import score_extraction
            from tools.trace_tool import update_trace_quality

            abstract_text = _get_document_abstract(document_id)
            score, note = await score_extraction(result, abstract_text)

            # Quality gate: retry once if score is below threshold
            if score < 2.5:
                logger.warning(
                    "Quality score %.1f below threshold for document %d; retrying step 1",
                    score, document_id,
                )
                if on_stage:
                    on_stage("品質未達標，重新搜尋")
                answer2, _sources2, run_id2 = await run_document_research_step1(document_id, on_stage=on_stage)
                if answer2 and answer2.strip() != "Unable to generate a response.":
                    core2, question2 = await asyncio.gather(
                        structure_research_step2(answer2),
                        generate_interest_step3(answer2),
                    )
                    result2 = {**core2, **question2}
                    score2, note2 = await score_extraction(result2, abstract_text)
                    if score2 > score:
                        logger.info(
                            "Retry improved quality %.1f → %.1f for document %d",
                            score, score2, document_id,
                        )
                        result, score, note = result2, score2, note2
                        answer, _sources, run_id = answer2, _sources2, run_id2

            result["quality_score"] = score
            result["quality_note"] = note
            update_trace_quality(run_id, quality_score=score)
        return result, run_id, answer, _sources


async def extract_document_summary(
    document_id: int,
    on_stage: Callable[[str], None] | None = None,
) -> tuple[dict, str]:
    """Compatibility wrapper for the full extraction pipeline."""
    result, run_id, _answer, _sources = await extract_document_summary_with_raw(document_id, on_stage)
    return result, run_id
