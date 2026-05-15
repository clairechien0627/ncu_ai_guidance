"""Research pipeline package.

All research logic lives in agent.py; the other modules implement the
LangGraph nodes (planner, retriever, reflector, writer) and shared state.
"""

from .agent import (
    _background_tasks,
    run_research_task,
    run_research_summary,
    trace_metadata,
)

__all__ = [
    "_background_tasks",
    "run_research_task",
    "run_research_summary",
    "trace_metadata",
]
