import asyncio

from agents.router_agent import route_request
from agents.research.research_graph import (
    _hard_max_searches,
)
from agents.research.state import ResearchState
from agents.research import trace_metadata as summary_trace_metadata
from agents.research.task_planner import CoverageItemModel, ResearchPlan, _clean_plan, fallback_research_plan
from prompting.loader import PROMPT_STACKS, load_stack
from prompting.registry import list_known_names
from prompting.registry import get, resolve, select, version


CANONICAL_PROMPTS = [
    "core",
    "retrieval_capability",
    "chat_mode",
    "summary_quality",
    "question_skill",
    "summary_structure",
    "question_generator",
    "evaluation_agent",
    "task_planner",
    "research_scheduler",
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
    route = asyncio.run(route_request("summary this document", [1]))
    assert route.prompt_version.startswith("sha256:")


def test_prompt_ab_selects_configured_variant(monkeypatch):
    monkeypatch.setenv("PROMPT_AB_TESTS", '{"question_task":["question_generator"]}')

    assert select("question_task", "thread-1") == "question_generator"
    prompt = resolve("question_task", "thread-1")
    assert prompt.base_name == "question_task"
    assert prompt.name == "question_generator"
    assert prompt.version == version("question_generator")


def test_router_reports_research_runtime_prompt(monkeypatch):
    from agents.router_agent import RouterDecision
    monkeypatch.setenv("PROMPT_AB_TESTS", '{"question_task":["question_generator"]}')
    async def fake_orchestrate(*args, **kwargs):
        return RouterDecision(agent_name="research", evaluate_after=False, reason="test")
    monkeypatch.setattr("agents.router_agent._orchestrate", fake_orchestrate)

    route = asyncio.run(route_request("summary this document", [1], thread_id="thread-1"))

    assert route.agent_name == "research"
    assert route.prompt_name == "research_writer"
    assert route.prompt_version == version("research_writer")


def test_prompt_ab_test_no_longer_changes_research_runtime_route(monkeypatch):
    from agents.router_agent import RouterDecision
    monkeypatch.setenv("PROMPT_AB_TESTS", '{"question_task":["question_generator"]}')
    async def fake_orchestrate(*args, **kwargs):
        return RouterDecision(agent_name="research", evaluate_after=False, reason="test")
    monkeypatch.setattr("agents.router_agent._orchestrate", fake_orchestrate)

    route = asyncio.run(route_request("summary this document", [1], thread_id="thread-1"))

    assert route.agent_name == "research"
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
    assert "primary_prompt_json" in meta
    assert "workflow_prompts_json" in meta
    assert isinstance(meta["prompt_stack_tokens"], int)
    assert meta["prompt_stack_tokens"] > 0


def test_research_runtime_stack_lists_actual_graph_prompts():
    stack = load_stack("research_runtime", "thread-1:1")

    assert stack.name == "research_runtime"
    assert [prompt.base_name for prompt in stack.prompts] == [
        "core",
        "task_planner",
        "research_scheduler",
        "research_planner",
        "research_reflector",
        "research_writer",
    ]
    assert stack.metadata()["prompt_name"] == "research_writer"


def test_research_node_stack_metadata_uses_node_as_primary_prompt():
    from agents.research.runtime_prompts import research_node_stack_metadata

    meta = research_node_stack_metadata("research_planner")
    stack_json = meta["prompt_stack_json"]

    assert meta["prompt_stack_name"] == "research_runtime"
    assert meta["base_prompt_name"] == "core"
    assert meta["prompt_name"] == "research_planner"
    assert "research_planner" in stack_json
    assert "research_planner" in meta["primary_prompt_json"]
    assert meta["workflow_prompts_json"] == stack_json


def test_evaluation_default_stack_lists_evaluation_prompt():
    stack = load_stack("evaluation_default", "thread-1:1")

    assert stack.name == "evaluation_default"
    assert [prompt.base_name for prompt in stack.prompts] == [
        "core",
        "evaluation_agent",
    ]
    assert stack.metadata()["prompt_name"] == "evaluation_agent"


def test_extract_step4_stack_lists_summary_quality_prompt():
    stack = load_stack("extract_step4", "thread-1:1")

    assert stack.name == "extract_step4"
    assert [prompt.base_name for prompt in stack.prompts] == [
        "core",
        "summary_quality",
    ]
    assert stack.metadata()["prompt_name"] == "summary_quality"


def _coverage_items():
    return [
        {"id": "research_motivation", "label": "研究動機", "required": True, "search_hints": ["動機"]},
        {"id": "research_methods", "label": "研究方法", "required": True, "search_hints": ["方法"]},
        {"id": "research_findings", "label": "研究成果", "required": True, "search_hints": ["成果"]},
        {"id": "research_limitations", "label": "研究限制", "required": True, "search_hints": ["限制"]},
    ]



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

    assert stack.name == "research_runtime"
    assert stack.metadata()["prompt_name"] == "research_writer"
