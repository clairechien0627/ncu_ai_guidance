"""Specialist agent facade package.

The first refactor phase keeps the proven legacy LangGraph implementation in
``agent.py`` and routes calls through small specialist modules. This gives the
API a stable Main Agent entry point while keeping rollback simple.
"""

from .main_agent import route_agent_message, route_agent_stream

__all__ = ["route_agent_message", "route_agent_stream"]
