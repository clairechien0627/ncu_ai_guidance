import asyncio
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Callable

from dotenv import load_dotenv

# 1. 載入環境變數
load_dotenv()

# 2. Windows 補丁
if sys.platform.startswith("win"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# 3. 日誌設定
logger = logging.getLogger(__name__)

# 4. 延遲匯入以確保環境變數生效
from config import Settings
settings = Settings()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from database import create_tables, SessionLocal
from agents.runner import setup_checkpointer
from services import job_service
from api import documents, summaries, jobs, chat, traces

def _init_tracing():
    """Initialize observability tools: Arize Phoenix and Langfuse."""
    
    # --- 1. Arize Phoenix (Local OTel) ---
    if settings.phoenix_enabled:
        try:
            from opentelemetry import trace
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import BatchSpanProcessor
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
            from opentelemetry.sdk.resources import Resource
            from openinference.instrumentation.langchain import LangChainInstrumentor
            from openinference.semconv.resource import ResourceAttributes

            # 定義資源與專案名稱 (使用底線 report_agent)
            # Phoenix 依賴 ResourceAttributes.PROJECT_NAME ("openinference.project.name")
            # 來決定 trace 要放進哪個 project，而非 "service.name"
            project_name = os.getenv("PHOENIX_PROJECT_NAME", "report_agent")
            resource = Resource(attributes={
                ResourceAttributes.PROJECT_NAME: project_name,
                "service.name": project_name,
            })
            tracer_provider = TracerProvider(resource=resource)
            
            # 設定 BatchSpanProcessor (gRPC 模式)
            endpoint = settings.phoenix_collector_endpoint
            span_exporter = OTLPSpanExporter(endpoint=endpoint, insecure=True)
            tracer_provider.add_span_processor(BatchSpanProcessor(span_exporter))
            
            # 設定為全域 Provider
            trace.set_tracer_provider(tracer_provider)
            
            # 4. 啟動全域自動儀表化
            if not LangChainInstrumentor().is_instrumented_by_opentelemetry:
                LangChainInstrumentor().instrument(tracer_provider=tracer_provider)
                
            logger.info("Arize Phoenix (Auto-Instrumented) initialized for project: %s", project_name)
        except Exception as exc:
            logger.warning("Failed to initialize Arize Phoenix: %s", exc)

    # --- 2. Langfuse (If keys are present) ---
    if settings.langfuse_public_key and settings.langfuse_secret_key:
        # Langfuse will be used via CallbackHandler in runner.py
        logger.info("Langfuse monitoring ready for project: %s", settings.langsmith_project)


def _run_migrations():
    """Apply pending Alembic migrations (idempotent, safe to run on every start)."""
    try:
        from alembic.config import Config as AlembicConfig
        from alembic import command as alembic_command
        logging.getLogger("alembic").setLevel(logging.WARNING)
        cfg = AlembicConfig(os.path.join(os.path.dirname(__file__), "alembic.ini"))
        alembic_command.upgrade(cfg, "head")
    except Exception as exc:
        logger.warning("Alembic migration failed (non-fatal): %s", exc)

app = FastAPI(title="Report Agent API")

_CORS_ORIGINS = [o.strip() for o in os.environ.get(
    "CORS_ORIGINS",
    "http://localhost:5173,http://localhost:3000"
).split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(documents.router)
app.include_router(summaries.router)
app.include_router(jobs.router)
app.include_router(chat.router)
app.include_router(traces.router)

@app.on_event("startup")
async def startup():
    _init_tracing()      # Start observability tools
    create_tables()      # CREATE TABLE IF NOT EXISTS for all models
    _run_migrations()    # ALTER TABLE / type conversions via Alembic
    await setup_checkpointer()
    job_service.reset_stuck_processing()
    job_service.init_workers()
    job_service.restore_jobs_from_db()
