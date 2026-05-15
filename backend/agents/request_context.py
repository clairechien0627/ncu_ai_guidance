"""Per-request context variables.

Each FastAPI request runs in its own asyncio task context, so ContextVar
values are isolated between requests.  asyncio.create_task() copies the
current context, so values set here propagate automatically into background
tasks (e.g., the research stage-event task in the streaming path).
"""

from contextvars import ContextVar

_user_id: ContextVar[str | None] = ContextVar("user_id", default=None)


def set_user_id(user_id: str | None) -> None:
    _user_id.set(user_id)


def get_user_id() -> str | None:
    return _user_id.get()
