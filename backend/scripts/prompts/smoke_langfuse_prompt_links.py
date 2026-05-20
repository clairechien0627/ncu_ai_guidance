"""Smoke-test Langfuse prompt-linked generations.

This script runs a small set of real LLM calls and prints Langfuse trace URLs.
It is intentionally narrow: it verifies prompt-link plumbing for controlled
paths without requiring the FastAPI server or LangGraph checkpointer.
"""

from __future__ import annotations

import asyncio
import os
import sys

from dotenv import load_dotenv

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
load_dotenv(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".env")))


async def main() -> None:
    from agents.evaluation_agent import evaluate_output
    from agents.router_agent import route_request
    from observability import initialize_langfuse_client
    from services.extraction_quality import score_extraction

    langfuse = initialize_langfuse_client()
    if langfuse is None:
        raise RuntimeError("Langfuse is not configured")

    traces: list[tuple[str, str | None]] = []

    with langfuse.start_as_current_observation(name="Smoke Summary Quality") as obs:
        score, note = await score_extraction(
            {
                "motivation": "本文探討學生如何理解研究文件。",
                "method": "使用文件片段整理與人工檢查。",
                "results": "結果顯示需要更明確的證據連結。",
                "tags": ["RAG", "evaluation"],
            },
            "This paper studies how students read research documents.",
        )
        obs.update(output={"score": score, "note": note})
        traces.append(("summary_quality", langfuse.get_current_trace_id()))

    with langfuse.start_as_current_observation(name="Smoke Evaluation Agent") as obs:
        result = await evaluate_output(
            user_task="評估這段回答是否有根據",
            answer="這個回答有引用來源，但缺少具體頁碼，因此證據支撐不足。",
            task_type="chat_turn",
            sources=["demo.pdf p.1"],
        )
        obs.update(output=result.model_dump())
        traces.append(("evaluation_agent", langfuse.get_current_trace_id()))

    with langfuse.start_as_current_observation(name="Smoke Intent Router") as obs:
        route = await route_request(
            "請判斷我接下來應該怎麼閱讀這份文件",
            document_ids=[1],
            thread_id="smoke-langfuse-prompts",
        )
        obs.update(output=route.__dict__)
        traces.append(("route_coordinator", langfuse.get_current_trace_id()))

    langfuse.flush()
    for name, trace_id in traces:
        url = langfuse.get_trace_url(trace_id=trace_id) if trace_id else None
        print(f"{name}: trace_id={trace_id} url={url}")


if __name__ == "__main__":
    asyncio.run(main())
