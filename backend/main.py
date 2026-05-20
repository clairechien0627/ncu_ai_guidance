import asyncio
import logging
import os
import sys
from typing import Callable

from dotenv import load_dotenv

# 1. 載入環境變數
load_dotenv()

# 2. Windows：psycopg3 與 ProactorEventLoop 不相容，強制使用 SelectorEventLoop
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# 3. 集中式 logging 設定（JSON 或純文字，由 LOG_FORMAT 環境變數控制）
from logging_config import configure_logging
configure_logging()

logger = logging.getLogger(__name__)

# 4. 延遲匯入以確保環境變數生效
from config import Settings
settings = Settings()

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from db import create_tables
from agents.runner import setup_checkpointer
from services import job_service
from api import documents, summaries, jobs, chat, traces, health, prompts, system, auth, users as users_api

def _init_tracing():
    """Initialize Langfuse observability."""
    try:
        from observability import auth_check_langfuse
        auth_check_langfuse()
    except Exception as exc:
        logger.warning("Failed to initialize Langfuse: %s", exc)


def _run_migrations() -> None:
    """Apply pending Alembic migrations.

    Raises on failure so the app never starts on a half-migrated schema.
    create_tables() is called first only to bootstrap a brand-new empty DB;
    Alembic then owns all schema evolution from that baseline.
    """
    from alembic.config import Config as AlembicConfig
    from alembic import command as alembic_command
    logging.getLogger("alembic").setLevel(logging.WARNING)
    cfg = AlembicConfig(os.path.join(os.path.dirname(__file__), "alembic.ini"))
    try:
        alembic_command.upgrade(cfg, "head")
    except Exception as exc:
        logger.critical("Alembic migration failed — refusing to start: %s", exc)
        raise SystemExit(1) from exc


def _reset_stuck_runs():
    """Reset experiment/eval runs stuck in 'running' state from a previous crash."""
    try:
        from services.evaluation.worker import EvaluationWorker
        result = EvaluationWorker.recover_stale_records(timeout_seconds=0)
        if any(result.values()):
            logger.info("startup recovery: %s", result)
    except Exception as exc:
        logger.warning("startup recovery failed: %s", exc)

@asynccontextmanager
async def lifespan(app: FastAPI):
    _init_tracing()
    create_tables()   # bootstrap empty DB; Alembic owns all evolution below
    _run_migrations() # fail-fast if any migration cannot apply
    await setup_checkpointer()
    from services.memory_service import ensure_memory_collection
    from services.redis_service import init_redis, close_redis
    ensure_memory_collection()
    await init_redis()
    job_service.reset_stuck_processing()
    _reset_stuck_runs()
    job_service.init_workers()
    from services.trace_ingestion import init_trace_ingestion_worker, stop_trace_ingestion_worker
    from services.evaluation.worker import init_evaluation_worker, stop_evaluation_worker
    init_trace_ingestion_worker()
    init_evaluation_worker()
    job_service.restore_jobs_from_db()
    yield
    await stop_evaluation_worker()
    await stop_trace_ingestion_worker()
    job_service.mark_all_interrupted()
    await job_service.shutdown_workers()
    await close_redis()
    # Signal all active research graph runs to stop at the next superstep boundary.
    from agents.research.agent import request_all_drain
    request_all_drain("server_shutdown")
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

@app.middleware("http")
async def auth_context_middleware(request: Request, call_next):
    """Read JWT from Authorization header and set user_id in request context."""
    from agents.request_context import set_user_id
    from services.auth_service import decode_token
    token = request.headers.get("Authorization", "")
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    if token:
        try:
            payload = decode_token(token)
            set_user_id(str(payload["sub"]))
        except Exception as exc:
            logger.debug("auth middleware: token decode failed: %s", exc)
    return await call_next(request)

app.include_router(auth.router)
app.include_router(users_api.router)
app.include_router(documents.router)
app.include_router(summaries.router)
app.include_router(jobs.router)
app.include_router(chat.router)
app.include_router(traces.router)
app.include_router(prompts.router)
app.include_router(health.router)
app.include_router(system.router)
