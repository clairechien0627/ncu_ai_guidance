"""Health check endpoint for service dependency monitoring."""
from fastapi import APIRouter
from sqlalchemy import text

router = APIRouter()


@router.get("/health")
async def health():
    """Check connectivity to all required services.

    Returns overall status and per-service details.
    Status values: "ok" | "degraded" | "error"
    """
    import asyncio
    services: dict[str, str] = {}
    overall = "ok"

    # PostgreSQL (main DB)
    try:
        from db import engine
        await asyncio.to_thread(_check_postgres, engine)
        services["postgres"] = "ok"
    except Exception as exc:
        services["postgres"] = f"error: {exc}"
        overall = "degraded"

    # LangGraph checkpointer (AsyncPostgresSaver)
    try:
        from agents.runner import get_checkpointer
        cp = get_checkpointer()
        services["checkpointer"] = "ok" if cp is not None else "not_initialized"
        if cp is None:
            overall = "degraded"
    except Exception as exc:
        services["checkpointer"] = f"error: {exc}"
        overall = "degraded"

    # LangGraph store (AsyncPostgresStore / pgvector)
    try:
        from agents.runner import get_store
        store = get_store()
        services["store"] = "ok" if store is not None else "not_initialized"
        if store is None:
            overall = "degraded"
    except Exception as exc:
        services["store"] = f"error: {exc}"
        overall = "degraded"

    # Qdrant
    try:
        await asyncio.to_thread(_check_qdrant)
        services["qdrant"] = "ok"
    except Exception as exc:
        services["qdrant"] = f"error: {exc}"
        overall = "degraded"

    return {"status": overall, "services": services}


def _check_postgres(engine) -> None:
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))


def _check_qdrant() -> None:
    from rag.store import get_qdrant_client
    client = get_qdrant_client()
    client.get_collections()
