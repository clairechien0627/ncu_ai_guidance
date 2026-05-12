import asyncio

from agents.main_agent import classify_intent
from agents.research.research_graph import (
    _hard_max_searches,
    _next_coverage_slot,
    _ready_for_verification,
    _verification_display_intent,
)
from agents.research.state import ResearchState
from agents.research_agent import trace_metadata as summary_trace_metadata
from agents.research.task_planner import CoverageItemModel, ResearchPlan, _clean_plan, fallback_research_plan
from prompting.loader import PROMPT_STACKS, load_stack
from prompting.registry import list_known_names
from prompts import get, resolve, select, version


CANONICAL_PROMPTS = [
    "core",
    "retrieval_capability",
    "chat_mode",
    "summary_mode",
    "summary_quality",
    "question_skill",
    "summary_structure",
    "question_generator",
    "intent_router",
    "task_planner",
    "research_planner",
    "research_reflector",
    "research_writer",
]


def test_prompt_version_is_content_hash():
    prompt_version = version("chat")
    assert prompt_version.startswith("sha256:")
    assert len(prompt_version) == len("sha256:") + 12


def test_all_canonical_prompts_exist_and_are_nonempty():
    known_names = set(list_known_names())

    for prompt_name in CANONICAL_PROMPTS:
        assert prompt_name in known_names
        assert get(prompt_name).strip()
        assert version(prompt_name).startswith("sha256:")


def test_all_prompt_stacks_load_nonempty_content():
    for stack_name, prompt_names in PROMPT_STACKS.items():
        stack = load_stack(stack_name, key="smoke-test")

        assert stack.name == stack_name
        assert len(stack.prompts) == len(prompt_names)
        assert stack.contents
        assert stack.metadata()["prompt_stack_name"] == stack_name
        assert stack.metadata()["prompt_stack_tokens"] > 0


def test_router_reports_prompt_hash_version():
    route = asyncio.run(classify_intent("summary this document", [1]))
    assert route.prompt_version.startswith("sha256:")


def test_prompt_ab_selects_configured_variant(monkeypatch):
    monkeypatch.setenv("PROMPT_AB_TESTS", '{"summary_task":["summary_structure"]}')

    assert select("summary_task", "thread-1") == "summary_structure"
    prompt = resolve("summary_task", "thread-1")
    assert prompt.base_name == "summary_task"
    assert prompt.name == "summary_structure"
    assert prompt.version == version("summary_structure")


def test_router_reports_research_runtime_prompt(monkeypatch):
    monkeypatch.setenv("PROMPT_AB_TESTS", '{"summary_task":["summary_structure"]}')

    route = asyncio.run(classify_intent("summary this document", [1], thread_id="thread-1"))

    assert route.intent == "research"
    assert route.prompt_name == "research_writer"
    assert route.prompt_version == version("research_writer")


def test_summary_task_ab_test_no_longer_changes_research_runtime_route(monkeypatch):
    monkeypatch.setenv("PROMPT_AB_TESTS", '{"summary_task":["summary_quality"]}')

    route = asyncio.run(classify_intent("summary this document", [1], thread_id="thread-1"))

    assert route.intent == "research"
    assert route.prompt_name == "research_writer"
    assert route.prompt_version == version("research_writer")


def test_summary_trace_metadata_includes_prompt_stack():
    meta = summary_trace_metadata(thread_id="thread-1", document_ids=[2, 1])

    assert meta["prompt_stack_name"] == "research_runtime"
    assert meta["base_prompt_name"] == "core"
    assert meta["task_prompt_name"] == "research_writer"
    assert meta["base_prompt_hash"] == version("core")
    assert meta["task_prompt_hash"] == version("research_writer")
    assert "prompt_stack_json" in meta
    assert isinstance(meta["prompt_stack_tokens"], int)
    assert meta["prompt_stack_tokens"] > 0


def test_prompt_stack_loader_uses_canonical_names_and_alias_sources():
    stack = load_stack("research_summary", "thread-1:1")

    assert stack.name == "research_summary"
    assert [prompt.base_name for prompt in stack.prompts] == [
        "core",
        "retrieval_capability",
        "summary_mode",
        "summary_quality",
    ]
    assert stack.prompts[1].source_name == "retrieval_capability"
    assert stack.prompts[2].source_name == "summary_mode"
    assert stack.metadata()["prompt_stack_name"] == "research_summary"


def test_research_runtime_stack_lists_actual_graph_prompts():
    stack = load_stack("research_runtime", "thread-1:1")

    assert stack.name == "research_runtime"
    assert [prompt.base_name for prompt in stack.prompts] == [
        "core",
        "task_planner",
        "research_planner",
        "research_reflector",
        "research_writer",
    ]
    assert stack.metadata()["prompt_name"] == "research_writer"


def _coverage_items():
    return [
        {"id": "research_motivation", "label": "研究動機", "required": True, "search_hints": ["動機"]},
        {"id": "research_methods", "label": "研究方法", "required": True, "search_hints": ["方法"]},
        {"id": "research_findings", "label": "研究成果", "required": True, "search_hints": ["成果"]},
        {"id": "research_limitations", "label": "研究限制", "required": True, "search_hints": ["限制"]},
    ]


def test_research_scheduler_keeps_coverage_order_until_slot_cap():
    coverage = _coverage_items()
    rs = ResearchState(
        question="q",
        document_ids=[1],
        coverage_items=coverage,
        slot_status={
            "research_motivation": "FILLED",
            "research_methods": "PARTIAL",
            "research_findings": "NOT_FILLED",
            "research_limitations": "NOT_FILLED",
        },
    )
    graph_state_before_cap = {
        "coverage_items": coverage,
        "steps_json": [{"slot": "research_methods"} for _ in range(6)],
        "max_searches": 10,
        "max_searches_per_slot": 7,
    }
    graph_state_at_cap = {
        **graph_state_before_cap,
        "steps_json": [{"slot": "research_methods"} for _ in range(7)],
    }

    assert _next_coverage_slot(graph_state_before_cap, rs) == "research_findings"
    assert _next_coverage_slot(graph_state_at_cap, rs) == "research_findings"


def test_research_scheduler_temporarily_skips_stalled_slot():
    coverage = _coverage_items()
    rs = ResearchState(
        question="q",
        document_ids=[1],
        coverage_items=coverage,
        slot_status={
            "research_motivation": "PARTIAL",
            "research_methods": "PARTIAL",
            "research_findings": "PARTIAL",
            "research_limitations": "NOT_FILLED",
        },
    )
    graph_state = {
        "coverage_items": coverage,
        "steps_json": [
            {"slot": "research_findings", "quality": "NO_RESULTS"},
            {"slot": "research_findings", "quality": "NOT_USEFUL"},
        ],
        "max_searches": 10,
        "max_searches_per_slot": 7,
    }

    assert _next_coverage_slot(graph_state, rs) == "research_limitations"


def test_research_verification_waits_for_direct_required_slot_attempts():
    coverage = _coverage_items()
    rs = ResearchState(
        question="q",
        document_ids=[1],
        coverage_items=coverage,
        search_count=2,
        slot_status={
            "research_motivation": "PARTIAL",
            "research_methods": "PARTIAL",
            "research_findings": "PARTIAL",
            "research_limitations": "PARTIAL",
        },
        verification_done=False,
    )
    graph_state = {
        "coverage_items": coverage,
        "steps_json": [
            {"slot": "research_motivation", "quality": "USEFUL"},
            {"slot": "research_methods", "quality": "USEFUL"},
        ],
        "max_searches": 10,
        "max_searches_per_slot": 7,
    }

    assert rs.ready_for_verification() is True
    assert _ready_for_verification(graph_state, rs) is False
    assert _next_coverage_slot(graph_state, rs) == "research_findings"

    graph_state["steps_json"].extend([
        {"slot": "research_findings", "quality": "USEFUL"},
        {"slot": "research_limitations", "quality": "NO_RESULTS"},
    ])

    assert _ready_for_verification(graph_state, rs) is True


def test_research_verification_display_intent_names_target_slot():
    rs = ResearchState(
        question="q",
        document_ids=[1],
        coverage_items=[
            {"id": "research_findings", "label": "研究成果", "required": True, "search_hints": ["成果"]},
        ],
    )

    assert _verification_display_intent(rs, "research_findings") == "補強研究成果證據"


def test_research_hard_cap_scales_by_coverage_count_and_per_slot_budget():
    graph_state = {
        "coverage_items": _coverage_items(),
        "steps_json": [],
        "max_searches": 10,
        "max_searches_per_slot": 7,
    }

    assert _hard_max_searches(graph_state) == 29


def test_task_planner_marks_explicitly_requested_optional_items_required():
    plan = ResearchPlan(
        goal="g",
        coverage_items=[
            CoverageItemModel(
                id="research_limitations",
                label="研究限制",
                description="探討研究限制或未解決問題。",
                required=False,
                search_hints=["研究限制", "未解決問題"],
                success_criteria="指出限制。",
            ),
            CoverageItemModel(
                id="notable_findings",
                label="有趣或有辨識度的發現",
                description="整理適合導讀或興趣量表的特色發現。",
                required=False,
                search_hints=["有趣發現", "導讀"],
                success_criteria="列出有趣例子。",
            ),
        ],
        output_contract="",
    )

    cleaned = _clean_plan(plan, "summary", "請保留限制，以及真正有趣或有辨識度的發現，讓後續能產生導讀與興趣量表。")

    assert all(item.required for item in cleaned.coverage_items)


def test_task_planner_fallback_uses_comparison_coverage_for_compare_questions():
    plan = fallback_research_plan("summary", "請比較兩份文件的方法與成果差異")

    ids = [item.id for item in plan.coverage_items]

    assert "comparison_dimensions" in ids
    assert "key_differences" in ids
    assert "research_motivation" not in ids


def test_task_planner_fallback_uses_learning_coverage_for_student_guides():
    plan = fallback_research_plan("summary", "請整理成高中生導讀與興趣量表素材")

    ids = [item.id for item in plan.coverage_items]

    assert "student_friendly_topic" in ids
    assert "learning_hooks" in ids
    assert "research_motivation" not in ids


def test_task_planner_fallback_uses_method_coverage_for_method_only_questions():
    plan = fallback_research_plan("summary", "請只整理研究方法、分析流程與具體步驟")

    ids = [item.id for item in plan.coverage_items]

    assert ids[:3] == ["method_overview", "data_or_materials", "procedure_steps"]
    assert "research_findings" not in ids


def test_prompt_stack_loader_keeps_extract_step1_alias():
    stack = load_stack("extract_step1", "thread-1:1")

    assert stack.name == "research_summary"
