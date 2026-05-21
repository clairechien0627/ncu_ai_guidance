# Backward-compatibility shim — real implementation moved to backend/context.py.
# All existing callers (agents/runner.py, main.py, api/chat.py) continue to work
# unchanged; observability/ now imports from context directly to break the cycle.
from context import get_user_id, set_user_id  # noqa: F401
