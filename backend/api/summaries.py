import asyncio
import json
import logging
import os

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from config import settings
from db import get_db, Document, SessionLocal
from rag import process_pdf
from services import job_service

logger = logging.getLogger(__name__)
router = APIRouter()



_DEFAULT_BATCH_FOLDER = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "各系大專生計畫(104-114)"))

def _get_batch_folder() -> str:
    folder = settings.batch_folder or _DEFAULT_BATCH_FOLDER
    if not settings.batch_folder:
        logger.warning(
            "BATCH_FOLDER not set in environment; falling back to default path '%s'. "
            "Set BATCH_FOLDER in .env for production use.",
            _DEFAULT_BATCH_FOLDER,
        )
    return folder


@router.post("/api/documents/{doc_id}/extract")
async def extract_summary(doc_id: int, db: Session = Depends(get_db)):
    doc = db.query(Document).filter(
        Document.id == doc_id,
        Document.status == "ready",
        Document.deleted_at.is_(None),
    ).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found or not ready")
    if job_service.jobs_has_active(doc_id):
        return {"id": doc_id, "filename": doc.filename, "queued": True}

    on_failure = "restore_summarized" if doc.summary_json else "error"
    doc.batch_status = "processing"
    db.commit()

    job_service.jobs_enqueue([(doc_id, doc.filename)], job_type="extract")
    await job_service.run_extraction_batch([(doc_id, doc.filename)], on_failure)
    await job_service.push_jobs()

    return {"id": doc_id, "filename": doc.filename, "queued": True}


@router.post("/api/documents/{doc_id}/extract-step/{step}")
async def extract_summary_step(step: str, doc_id: int, db: Session = Depends(get_db)):
    if step not in {"step1", "step2", "step3"}:
        raise HTTPException(status_code=400, detail="step must be step1, step2, or step3")
    doc = db.query(Document).filter(
        Document.id == doc_id,
        Document.status == "ready",
        Document.deleted_at.is_(None),
    ).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found or not ready")
    if step in {"step2", "step3"} and not doc.raw_research_answer:
        raise HTTPException(status_code=400, detail="請先執行 Step 1，才能重跑 Step 2 或 Step 3")
    if job_service.jobs_has_active(doc_id):
        return {"id": doc_id, "filename": doc.filename, "queued": True}

    on_failure = "restore_summarized" if doc.summary_json else "error"
    doc.batch_status = "processing"
    db.commit()

    job_service.jobs_enqueue([(doc_id, doc.filename)], job_type=f"extract_{step}")
    await job_service.run_extraction_step_batch([(doc_id, doc.filename)], step, on_failure)
    await job_service.push_jobs()

    return {"id": doc_id, "filename": doc.filename, "queued": True}


@router.get("/api/summaries")
def list_summaries(slim: bool = False, db: Session = Depends(get_db)):
    if not slim:
        from rag import _pymupdf_cache_path, _azure_di_cache_path, _llamaparse_cache_path
    docs = db.query(Document).filter(
        Document.deleted_at.is_(None),
        Document.status.in_(["ready", "processing", "error"])
    ).order_by(Document.created_at.asc()).all()
    return [
        {
            "id": d.id,
            "filename": d.filename,
            "department": (d.extraction.department_hint if d.extraction else None) or d.department_hint or "其他",
            "status": d.status,
            "batch_status": d.batch_status or "pending",
            "summary": json.loads(d.extraction.summary_json) if d.extraction and d.extraction.summary_json else None,
            "raw_research_answer": d.extraction.raw_research_answer if d.extraction else None,
            "raw_research_sources": json.loads(d.extraction.raw_research_sources) if d.extraction and d.extraction.raw_research_sources else [],
            "created_at": d.created_at,
            "abstract_text": d.abstract_text,
            "quality_issue": d.quality_issue,
            "parser_used": d.parser_used,
            "needs_reindex": d.needs_reindex,
            **({"caches": {
                "pymupdf4llm": os.path.exists(_pymupdf_cache_path(d.id)),
                "azure_di": os.path.exists(_azure_di_cache_path(d.id)),
                "llamaparse": os.path.exists(_llamaparse_cache_path(d.id)),
            }} if not slim else {}),
        }
        for d in docs
    ]


@router.post("/api/summaries/batch-import")
async def batch_import(db: Session = Depends(get_db)):
    batch_folder = _get_batch_folder()
    if not os.path.isdir(batch_folder):
        raise HTTPException(status_code=404, detail=f"找不到資料夾: {batch_folder}")

    all_pdfs = []
    for root, _dirs, files in os.walk(batch_folder):
        for fname in sorted(files):
            if fname.lower().endswith(".pdf"):
                dept = os.path.basename(root) if root != batch_folder else "未分類"
                all_pdfs.append((os.path.join(root, fname), fname, dept))

    existing_docs = {
        d.filename: d
        for d in db.query(Document).filter(Document.deleted_at.is_(None)).all()
    }

    updated = 0
    for _path, fname, dept in all_pdfs:
        doc = existing_docs.get(fname)
        if doc and not doc.department_hint:
            doc.department_hint = dept
            updated += 1
    if updated:
        db.commit()

    new_pdfs = [(p, f, d) for p, f, d in all_pdfs if f not in existing_docs]
    error_pdfs = [(p, f, d) for p, f, d in all_pdfs
                  if f in existing_docs and existing_docs[f].status == "error"]

    to_queue = new_pdfs + error_pdfs
    if not to_queue:
        return {"queued": 0, "already_imported": len(all_pdfs) - len(error_pdfs), "dept_updated": updated}

    doc_records: list[tuple[int, str]] = []
    for file_path, fname, dept in new_pdfs:
        doc = Document(filename=fname, file_path=file_path, status="processing", department_hint=dept)
        db.add(doc)
        db.flush()
        doc_records.append((doc.id, file_path))
    for file_path, fname, _dept in error_pdfs:
        doc = existing_docs[fname]
        doc.status = "processing"
        doc_records.append((doc.id, file_path))
    db.commit()

    names = [fname for _, fname, _ in to_queue]
    job_service.jobs_enqueue(list(zip([did for did, _ in doc_records], names)), job_type="reindex")
    for doc_id, file_path in doc_records:
        await job_service.enqueue_reindex_job({"doc_id": doc_id, "file_path": file_path, "tmp_path": None, "parser": "auto"})
    await job_service.push_jobs()
    return {"queued": len(new_pdfs), "retried": len(error_pdfs), "already_imported": len(all_pdfs) - len(to_queue), "dept_updated": updated}


@router.post("/api/summaries/batch-extract")
async def batch_extract_all(background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    pending = db.query(Document).filter(
        Document.status == "ready",
        Document.batch_status == "pending",
        Document.deleted_at.is_(None),
    ).all()

    if not pending:
        return {"queued": 0}

    pairs = []
    for doc in pending:
        doc.batch_status = "processing"
        pairs.append((doc.id, doc.filename))
    db.commit()
    job_service.jobs_enqueue(pairs, job_type="extract")
    await job_service.push_jobs()
    await job_service.run_extraction_batch(pairs, "error")
    return {"queued": len(pairs)}


@router.post("/api/summaries/batch-reextract")
async def batch_reextract(background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    from db import DocumentExtraction
    from sqlalchemy import exists as _exists
    has_summary = _exists().where(
        DocumentExtraction.document_id == Document.id,
        DocumentExtraction.summary_json.isnot(None),
    )
    broken = db.query(Document).filter(
        Document.status == "ready",
        Document.batch_status == "error",
        has_summary,
        Document.deleted_at.is_(None),
    ).all()
    for doc in broken:
        doc.batch_status = "summarized"
    if broken:
        db.commit()

    docs = db.query(Document).filter(
        Document.status == "ready",
        Document.batch_status == "summarized",
        Document.deleted_at.is_(None),
    ).all()

    if not docs:
        return {"queued": 0}

    pairs = []
    for doc in docs:
        doc.batch_status = "processing"
        pairs.append((doc.id, doc.filename))
    db.commit()
    job_service.jobs_enqueue(pairs, job_type="extract")
    await job_service.push_jobs()
    await job_service.run_extraction_batch(pairs, "restore_summarized")
    return {"queued": len(pairs)}


@router.post("/api/summaries/batch-reextract-all")
async def batch_reextract_all_docs(background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    docs = db.query(Document).filter(
        Document.status == "ready",
        Document.batch_status == "summarized",
        Document.deleted_at.is_(None),
    ).all()

    if not docs:
        return {"queued": 0}

    pairs = []
    for doc in docs:
        doc.batch_status = "processing"
        pairs.append((doc.id, doc.filename))
    db.commit()
    job_service.jobs_enqueue(pairs, job_type="extract")
    await job_service.push_jobs()
    await job_service.run_extraction_batch(pairs, "restore_summarized")
    return {"queued": len(pairs)}


class BatchExtractSelectedRequest(BaseModel):
    doc_ids: list[int]
    force: bool = False


@router.post("/api/summaries/batch-extract-selected")
async def batch_extract_selected(req: BatchExtractSelectedRequest, db: Session = Depends(get_db)):
    query = db.query(Document).filter(
        Document.id.in_(req.doc_ids),
        Document.status == "ready",
        Document.deleted_at.is_(None),
    )
    if not req.force:
        query = query.filter(Document.batch_status.in_(["pending", "error"]))
    docs = query.all()

    if not docs:
        return {"queued": 0}

    pairs = []
    for doc in docs:
        doc.batch_status = "processing"
        pairs.append((doc.id, doc.filename))
    db.commit()

    job_service.jobs_enqueue(pairs, job_type="extract")
    await job_service.push_jobs()
    await job_service.run_extraction_batch(pairs, "restore_summarized" if req.force else "error")
    return {"queued": len(pairs)}


class BatchReparseRequest(BaseModel):
    parser: str
    doc_ids: list[int] | None = None


@router.post("/api/summaries/batch-reparse")
async def batch_reparse(req: BatchReparseRequest, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    if req.parser not in ("pymupdf4llm", "azure_di", "llamaparse"):
        raise HTTPException(status_code=400, detail="parser 必須是 pymupdf4llm / azure_di / llamaparse")

    if req.doc_ids:
        docs = db.query(Document).filter(
            Document.id.in_(req.doc_ids),
            Document.status == "ready",
            Document.deleted_at.is_(None),
        ).all()
    else:
        docs = db.query(Document).filter(
            Document.status == "ready",
            Document.deleted_at.is_(None),
        ).all()

    if not docs:
        return {"queued": 0}

    parser_label = {"pymupdf4llm": "本地解析", "azure_di": "Azure DI", "llamaparse": "LlamaParse"}.get(req.parser, req.parser)
    records = [
        (doc.id, doc.filename, doc.file_path)
        for doc in docs
        if not job_service.jobs_has_active_parse(doc.id, req.parser)
    ]

    if not records:
        return {"queued": 0}

    import uuid as _uuid
    job_ids = {}
    for doc_id, filename, _ in records:
        job_id = _uuid.uuid4().hex[:12]
        job_ids[doc_id] = job_id
        job_service.jobs_append({
            "doc_id": doc_id,
            "filename": filename,
            "status": "queued",
            "job_type": f"parse_{req.parser}",
            "stage": parser_label,
            "job_id": job_id,
        })
    await job_service.enqueue_parse_jobs([
        {
            "doc_id": doc_id,
            "filename": filename,
            "file_path": raw_path,
            "parser": req.parser,
            "job_id": job_ids[doc_id],
        }
        for doc_id, filename, raw_path in records
    ])
    return {"queued": len(records)}
