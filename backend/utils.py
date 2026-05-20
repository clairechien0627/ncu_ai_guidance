"""UUID generation utilities.

All system IDs (trace_id, observation_id, thread_id, etc.) use UUID7:
- Time-ordered: sortable without extra created_at lookup
- Monotonically increasing: better B-tree index performance
- Embeds creation timestamp in the UUID itself

Usage:
    from utils import new_id
    trace_id = new_id()
"""

from uuid_extensions import uuid7str as _uuid7str


def new_id() -> str:
    """Generate a new UUID7 string."""
    return _uuid7str()
