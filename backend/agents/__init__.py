"""Agent facade package.

Routing is owned by router_agent; task-agent modules implement task behavior.
"""

from .router_agent import route_agent_message, route_agent_stream

__all__ = ["route_agent_message", "route_agent_stream"]
