"""Sub-agent tools that allow the orchestrator to delegate to specialist agents.

Each tool wraps a specialist agent's answer() function as a LangChain tool,
so the orchestrator (orchestrator_agent) can call them inside its ReAct loop and
incorporate the result into its own response.

Sources found by sub-agents are accumulated in AgentContext.tool_sources so
run_orchestrator_agent can merge them into the final AgentResult.sources.
"""
import logging
import uuid
from langchain.tools import ToolRuntime, tool

from tools.rag_tool import AgentContext

logger = logging.getLogger(__name__)


@tool
async def call_question_agent(
    message: str,
    runtime: ToolRuntime[AgentContext],
) -> str:
    """Generate comprehension questions or a quiz from the uploaded documents.

    Use when the user asks to be tested, wants practice questions, requests
    guided reading, or uses words like: 出題、問我、測驗、題目、導讀、帶我學。
    """
    from agents.question_agent import answer as _qa_answer
    ctx: AgentContext = runtime.context
    try:
        result = await _qa_answer(
            message,
            ctx.thread_id or "",
            ctx.document_ids,
            run_id=str(uuid.uuid4()),
            parent_run_id=ctx.run_id,
        )
        if result.sources:
            ctx.tool_sources.extend(result.sources)
        return result.response
    except Exception as exc:
        logger.warning("call_question_agent failed: %s", exc)
        return f"[題目生成失敗：{exc}。請嘗試直接搜尋文件內容。]"


@tool
async def call_retrieval_agent(
    message: str,
    runtime: ToolRuntime[AgentContext],
) -> str:
    """Do focused document retrieval for precise factual questions.

    Use when the user asks something specific about a document that requires
    targeted search: page references, specific sections, exact citations,
    or when you tried a search already but the result was insufficient.
    """
    from agents.retrieval_agent import answer as _ret_answer
    ctx: AgentContext = runtime.context
    try:
        result = await _ret_answer(
            message,
            ctx.thread_id or "",
            ctx.document_ids,
            run_id=str(uuid.uuid4()),
            parent_run_id=ctx.run_id,
        )
        if result.sources:
            ctx.tool_sources.extend(result.sources)
        return result.response
    except Exception as exc:
        logger.warning("call_retrieval_agent failed: %s", exc)
        return f"[文件檢索失敗：{exc}。請嘗試使用 search_report 工具直接搜尋。]"


@tool
async def call_research_agent(
    message: str,
    runtime: ToolRuntime[AgentContext],
) -> str:
    """Run a comprehensive multi-aspect research analysis on the documents.

    Use when the user wants a structured summary, full analysis, or asks for
    comprehensive coverage: 摘要、總結、重點整理、懶人包、研究動機/方法/成果/限制、
    summary、overview。
    """
    from agents.research_agent import run_research_task
    ctx: AgentContext = runtime.context
    try:
        result = await run_research_task(
            question=message,
            thread_id=ctx.thread_id or "",
            document_ids=ctx.document_ids or [],
            mode="research",
            metadata={"mode": "research", "agent_name": "research_agent"},
            run_id=str(uuid.uuid4()),
            on_stage=ctx.on_stage,
            max_searches=10,
            max_consecutive_no_new=2,
            parent_run_id=ctx.run_id,
        )
        if result.sources:
            ctx.tool_sources.extend(result.sources)
        return result.response
    except Exception as exc:
        logger.warning("call_research_agent failed: %s", exc)
        return f"[深度研究失敗：{exc}。請嘗試使用 search_report 工具手動查詢各面向。]"


AGENT_TOOLS = [call_question_agent, call_retrieval_agent, call_research_agent]
