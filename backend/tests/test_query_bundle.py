import asyncio

from agents.research.planner import build_slot_decision
from agents.research.reflector import CoverageUpdate, Reflection, apply_reflection, reflect_results
from agents.research.state import ResearchState
from tools.rag_tool import expand_queries, set_query_expander_llm


def test_build_slot_decision_returns_query_bundle():
    state = ResearchState(
        question="整理研究成果",
        document_ids=[44],
        coverage_items=[
            {
                "id": "research_findings",
                "label": "研究成果",
                "required": True,
                "description": "整理作者的主要發現與水意象分類。",
                "search_hints": ["研究成果", "結論", "分類"],
                "success_criteria": "列出具體成果與代表例子。",
            }
        ],
        known_keywords=["滋養之水", "阻隔之水"],
    )

    decision = build_slot_decision(state, "research_findings")
    bundle = decision.query_bundle()

    assert decision.display_intent == "尋找研究成果"
    assert "研究成果" in decision.keyword_query
    assert "滋養之水" in decision.keyword_query
    assert "研究成果" in decision.semantic_query
    assert "結論" in decision.section_terms
    assert bundle["keyword_query"] == decision.keyword_query
    assert bundle["use_hyde"] is False


def test_build_slot_decision_enables_hyde_for_derived_slot_after_context_exists():
    state = ResearchState(
        question="整理適合高中生導讀與興趣量表的有趣發現",
        document_ids=[44],
        search_count=2,
        coverage_items=[
            {
                "id": "notable_findings",
                "label": "有趣或有辨識度的發現",
                "required": True,
                "description": "整理研究中特別有趣或具辨識度的發現，適合用於高中生導讀或興趣量表。",
                "search_hints": ["有趣發現", "辨識度高的內容", "高中生導讀"],
                "success_criteria": "提供能吸引高中生興趣的具體發現或例子。",
            }
        ],
        evidence={"notable_findings": ["p.27: 水意象與生命、創生有關。"]},
        slot_status={"notable_findings": "PARTIAL"},
    )

    decision = build_slot_decision(state, "notable_findings")

    assert decision.use_hyde is True
    assert decision.query_bundle()["use_hyde"] is True
    assert "已知證據詞" in decision.semantic_query


def test_build_slot_decision_does_not_use_hyde_on_first_round():
    state = ResearchState(
        question="整理適合高中生導讀與興趣量表的有趣發現",
        document_ids=[44],
        coverage_items=[
            {
                "id": "notable_findings",
                "label": "有趣或有辨識度的發現",
                "required": True,
                "description": "整理研究中特別有趣或具辨識度的發現，適合用於高中生導讀或興趣量表。",
                "search_hints": ["有趣發現", "高中生導讀"],
                "success_criteria": "提供具體發現。",
            }
        ],
    )

    decision = build_slot_decision(state, "notable_findings")

    assert decision.use_hyde is False


def test_expand_queries_uses_bundle_roles_without_hyde_by_default():
    queries = expand_queries(
        query="研究成果 滋養之水",
        keyword_query="研究成果 滋養之水 阻隔之水",
        semantic_query="文件說明《詩經》水意象如何形成分類與情感規則。",
        section_terms=["結論", "研究成果"],
        sub_queries=["文件說明《詩經》水意象如何形成分類與情感規則。", "研究成果 滋養之水"],
    )

    assert queries == [
        "研究成果 滋養之水 阻隔之水",
        "文件說明《詩經》水意象如何形成分類與情感規則。",
        "結論 研究成果",
        "研究成果 滋養之水",
    ]


def test_expand_queries_supports_legacy_query_shape():
    queries = expand_queries(
        query="研究方法 分類歸納 文本分析",
        sub_queries=["分析研究法", "對比詮釋法", "第三個會被忽略"],
    )

    assert queries == ["研究方法 分類歸納 文本分析", "分析研究法", "對比詮釋法"]


def test_expand_queries_can_append_hyde_when_enabled():
    class FakeLLM:
        def invoke(self, _messages):
            class Response:
                content = "文件可能描述水意象如何成為高中生導讀中的亮點。"

            return Response()

    set_query_expander_llm(FakeLLM())
    try:
        queries = expand_queries(
            query="有趣發現 水意象",
            keyword_query="有趣發現 水意象",
            semantic_query="尋找適合高中生導讀的水意象亮點。",
            section_terms=["小結", "結論"],
            use_hyde=True,
        )
    finally:
        set_query_expander_llm(None)

    assert queries[-1] == "文件可能描述水意象如何成為高中生導讀中的亮點。"


def test_apply_reflection_can_update_multiple_slots():
    state = ResearchState(
        question="整理摘要",
        document_ids=[44],
        coverage_items=[
            {"id": "research_methods", "label": "研究方法", "required": True, "description": "方法", "search_hints": [], "success_criteria": ""},
            {"id": "research_findings", "label": "研究成果", "required": True, "description": "成果", "search_hints": [], "success_criteria": ""},
        ],
    )
    reflection = Reflection(
        quality="USEFUL",
        filled_item="research_methods",
        updates=[
            CoverageUpdate(item_id="research_methods", status="FILLED", notes=["p.10: 使用分類歸納法。"]),
            CoverageUpdate(item_id="research_findings", status="PARTIAL", notes=["p.33: 提到滋養之水與阻隔之水。"]),
        ],
        new_keywords=["分類歸納法", "滋養之水"],
        missing_gap="研究限制尚未找到。",
        next_search_angle="研究限制",
        suggested_query_terms=["研究限制"],
        avoid_query_terms=[],
        rationale="方法證據也支援成果。",
    )

    changed = apply_reflection(state, reflection)

    assert changed is True
    assert state.slot_status["research_methods"] == "FILLED"
    assert state.slot_status["research_findings"] == "PARTIAL"
    assert state.evidence["research_findings"] == ["p.33: 提到滋養之水與阻隔之水。"]


def test_reflector_cross_slot_heuristic_adds_supported_other_slot():
    class FailingLLM:
        def with_structured_output(self, *args, **kwargs):
            raise RuntimeError("force fallback")

    state = ResearchState(
        question="整理摘要",
        document_ids=[44],
        coverage_items=[
            {"id": "research_methods", "label": "研究方法", "required": True, "description": "方法", "search_hints": ["分類歸納"], "success_criteria": ""},
            {"id": "research_findings", "label": "研究成果", "required": True, "description": "成果", "search_hints": ["結論", "滋養之水"], "success_criteria": ""},
        ],
    )
    chunks = [
        {
            "page": 33,
            "section": "background",
            "content": "文中提到水意象主要呈現滋養之水與阻隔之水，這是重要研究成果。",
            "is_low_quality": False,
        }
    ]

    reflection = asyncio.run(
        reflect_results(
            FailingLLM(),
            state=state,
            slot="research_methods",
            query="研究方法",
            chunks=chunks,
        )
    )

    updated = {update.item_id for update in reflection.updates}
    assert "research_findings" in updated


def test_reflector_does_not_treat_prior_work_critique_as_current_limitation():
    class FailingLLM:
        def with_structured_output(self, *args, **kwargs):
            raise RuntimeError("force fallback")

    state = ResearchState(
        question="整理研究限制",
        document_ids=[44],
        coverage_items=[
            {
                "id": "research_limitations",
                "label": "研究限制",
                "required": True,
                "description": "本文研究限制",
                "search_hints": ["研究限制", "不足", "未解決"],
                "success_criteria": "只收錄作者對本文的限制，不收錄文獻回顧中對前人研究的批評。",
            }
        ],
    )
    chunks = [
        {
            "filename": "金鴻達.pdf",
            "page": 8,
            "section": "introduction",
            "content": "康氏進一步採用原型批評理論，從環境、原始崇拜、社會風俗來探討水意象形成的原因，雖對水意象的研究更加廣，性名類別的討論較不足，殊為可惜。",
            "is_low_quality": False,
        }
    ]

    reflection = asyncio.run(
        reflect_results(
            FailingLLM(),
            state=state,
            slot="research_limitations",
            query="研究限制",
            chunks=chunks,
        )
    )
    changed = apply_reflection(state, reflection)

    assert reflection.updates[0].status == "NOT_FILLED"
    assert state.slot_status["research_limitations"] == "NOT_FILLED"
    assert state.evidence["research_limitations"] == []
    assert changed is True  # keywords/reflection metadata may still be updated
