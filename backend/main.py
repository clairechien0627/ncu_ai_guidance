import asyncio
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Callable

from dotenv import load_dotenv

# 1. 載入環境變數
load_dotenv()

# 2. 日誌設定（Windows event loop policy 由 run_dev_server.py 處理）
logger = logging.getLogger(__name__)

# 4. 延遲匯入以確保環境變數生效
from config import Settings
settings = Settings()

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from db import create_tables, SessionLocal
from agents.runner import setup_checkpointer
from services import job_service
from api import documents, summaries, jobs, chat, traces

def _init_tracing():
    """Initialize Langfuse observability."""
    try:
        from observability import auth_check_langfuse
        auth_check_langfuse()
    except Exception as exc:
        logger.warning("Failed to initialize Langfuse: %s", exc)


def _run_migrations():
    """Apply pending Alembic migrations (idempotent, safe to run on every start)."""
    try:
        from alembic.config import Config as AlembicConfig
        from alembic import command as alembic_command
        logging.getLogger("alembic").setLevel(logging.WARNING)
        cfg = AlembicConfig(os.path.join(os.path.dirname(__file__), "alembic.ini"))
        alembic_command.upgrade(cfg, "head")
    except Exception as exc:
        logger.error("Alembic migration failed: %s", exc)

@asynccontextmanager
async def lifespan(app: FastAPI):
    _init_tracing()
    create_tables()
    _run_migrations()
    await setup_checkpointer()
    from services.memory_service import ensure_memory_collection
    ensure_memory_collection()
    job_service.reset_stuck_processing()
    job_service.init_workers()
    job_service.restore_jobs_from_db()
    yield
    job_service.mark_all_interrupted()
    # Drain any in-flight background tasks (e.g. quality checks) before shutdown.
    from agents.research import _background_tasks
    if _background_tasks:
        await asyncio.gather(*list(_background_tasks), return_exceptions=True)


app = FastAPI(title="Report Agent API", lifespan=lifespan)

_CORS_ORIGINS = [o.strip() for o in os.environ.get(
    "CORS_ORIGINS",
    "http://localhost:5173,http://localhost:3000"
).split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Requested-With"],
)

app.include_router(documents.router)
app.include_router(summaries.router)
app.include_router(jobs.router)
app.include_router(chat.router)
app.include_router(traces.router)
