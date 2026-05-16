import asyncio
import datetime
import hashlib
import json
import os
import tempfile
from urllib.parse import quote

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, UploadFile, File

logger = logging.getLogger(__name__)
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from config import settings
from db import get_db, Document, SessionLocal
from qdrant_client.models import Filter, FieldCondition, MatchValue
from rag import (
    process_pdf, delete_document_vectors, get_qdrant_client, COLLECTION_NAME,
    _llamaparse_cache_path, _llamaparse_raw_cache_path,
    _azure_di_cache_path, _pymupdf_cache_path,
    update_document_vector_filename,
)
from services import job_service, storage_service

router = APIRouter()


def _sync_vector_filename(doc_id: int, filename: str) -> None:
    try:
        update_document_vector_filename(doc_id, filename)
    except Exception:
        logger.warning("vector filename sync failed for doc %s", doc_id, exc_info=True)


def _copy_upload_and_hash(file: UploadFile, file_path: str) -> str:
    digest = hashlib.sha256()
    with open(file_path, "wb") as f:
        while True:
            chunk = file.file.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            f.write(chunk)
    return digest.hexdigest()


def _unique_upload_name(filename: str, db: Session) -> str:
    stem, ext = os.path.splitext(filename)
    used = {
        name
        for (name,) in db.query(Document.filename).filter(Document.deleted_at.is_(None)).all()
    }
    candidate = filename
    index = 2
    while candidate in used or os.path.exists(os.path.join(settings.upload_dir, candidate)):
        candidate = f"{stem} ({index}){ext}"
        index += 1
    return candidate


@router.post("/api/upload")
async def upload_document(file: UploadFile = File(...), db: Session = Depends(get_db)):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported")

    os.makedirs(settings.upload_dir, exist_ok=True)
    upload_name = _unique_upload_name(file.filename, db)
    file_path = os.path.join(settings.upload_dir, upload_name)
    file_hash = _copy_upload_and_hash(file, file_path)

    existing_by_hash = db.query(Document).filter(
        Document.file_hash == file_hash,
        Document.deleted_at.is_(None),
        Document.status.in_(["ready", "processing", "error"]),
    ).first()
    if existing_by_hash:
        if os.path.exists(file_path):
            os.remove(file_path)
        return {
            "id": existing_by_hash.id,
            "filename": existing_by_hash.filename,
            "status": existing_by_hash.status,
            "chunks": 0,
            "duplicate": True,
            "duplicate_reason": "same_file_hash",
        }

    existing = db.query(Document).filter(
        Document.filename == file.filename,
        Document.status == "ready",
        Document.deleted_at.is_(None),
    ).first()
    if existing and not existing.file_hash:
        if os.path.exists(file_path):
            os.remove(file_path)
        return {"id": existing.id, "filename": existing.filename, "status": existing.status, "chunks": 0, "duplicate": True, "duplicate_reason": "same_filename_legacy"}

    blob_name = upload_name if storage_service._USE_BLOB else None
    if storage_service._USE_BLOB:
        storage_service.upload_file(file_path, blob_name)

    doc = Document(
        filename=upload_name,
        file_path=blob_name if storage_service._USE_BLOB else file_path,
        file_hash=file_hash,
        status="processing",
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)

    job_service.jobs_enqueue([(doc.id, doc.filename)], job_type="reindex")
    await job_service.enqueue_reindex_job({"doc_id": doc.id, "file_path": file_path, "tmp_path": None, "parser": None})
    await job_service.push_jobs()

    return {"id": doc.id, "filename": doc.filename, "status": "processing", "queued": True, "duplicate": False}


@router.get("/api/documents")
def list_documents(db: Session = Depends(get_db)):
    docs = db.query(Document).filter(Document.deleted_at.is_(None)).order_by(Document.created_at.desc()).all()
    return [
        {"id": d.id, "filename": d.filename, "status": d.status, "created_at": d.created_at, "needs_reindex": d.needs_reindex}
        for d in docs
    ]


@router.get("/api/documents/{doc_id}/file")
def get_document_file(doc_id: int, db: Session = Depends(get_db)):
    doc = db.query(Document).filter(Document.id == doc_id, Document.deleted_at.is_(None)).first()
    if not doc:
        raise HTTPException(status_code=404, detail="File not found")
    if storage_service._USE_BLOB:
        try:
            data = storage_service.read_blob(doc.file_path)
        except Exception:
            raise HTTPException(status_code=404, detail="File not found in blob storage")
        return Response(
            content=data,
            media_type="application/pdf",
            headers={"Content-Disposition": f"inline; filename*=UTF-8''{quote(doc.filename)}"},
        )
    if not os.path.exists(doc.file_path):
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(
        doc.file_path,
        media_type="application/pdf",
        headers={"Content-Disposition": f"inline; filename*=UTF-8''{quote(doc.filename)}"},
    )


class RenameRequest(BaseModel):
    new_title: str


@router.patch("/api/documents/{doc_id}/rename")
def rename_document(doc_id: int, req: RenameRequest, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    import re
    from pathlib import Path
    new_title = req.new_title.strip()
    if not new_title:
        raise HTTPException(status_code=400, detail="標題不可為空")
    if any(c in new_title for c in ('/', '\\', '\0', ':', '*', '?', '"', '<', '>', '|')):
        raise HTTPException(status_code=400, detail="標題含有非法字元")

    doc = db.query(Document).filter(Document.id == doc_id, Document.deleted_at.is_(None)).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    old_filename = doc.filename
    # Preserve the "projectNo_dept_" prefix (e.g. "112_CS_") if the original
    # filename follows that convention; otherwise use the bare title.
    m = re.match(r'^([^_]+_[^_]+_)', old_filename)
    prefix = m.group(1) if m else ''
    new_filename = f"{prefix}{new_title}.pdf"

    if new_filename == old_filename:
        return {"id": doc_id, "filename": old_filename}

    if db.query(Document).filter(
        Document.filename == new_filename,
        Document.deleted_at.is_(None),
        Document.id != doc_id,
    ).first():
        raise HTTPException(status_code=409, detail="同名檔案已存在")

    if not storage_service._USE_BLOB and doc.file_path:
        old_path = Path(doc.file_path)
        if old_path.exists():
            new_path = old_path.parent / new_filename
            try:
                old_path.replace(new_path)
                doc.file_path = str(new_path)
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"無法重新命名檔案：{e}")

    doc.filename = new_filename
    db.commit()
    background_tasks.add_task(_sync_vector_filename, doc_id, new_filename)
    return {"id": doc_id, "filename": new_filename}


class ParseRequest(BaseModel):
    parser: str


@router.post("/api/documents/{doc_id}/parse")
async def parse_document(doc_id: int, req: ParseRequest, db: Session = Depends(get_db)):
    if req.parser not in ("pymupdf4llm", "azure_di", "llamaparse"):
        raise HTTPException(status_code=400, detail="parser 必須是 pymupdf4llm / azure_di / llamaparse")

    doc = db.query(Document).filter(Document.id == doc_id, Document.deleted_at.is_(None)).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    if job_service.jobs_has_active_parse(doc_id, req.parser):
        return {"id": doc_id, "parser": req.parser, "queued": True, "duplicate": True}

    if not storage_service._USE_BLOB and not os.path.exists(doc.file_path):
        raise HTTPException(status_code=404, detail="原始檔案不存在於磁碟")

    parser_label = {"pymupdf4llm": "本地解析", "azure_di": "Azure DI 解析", "llamaparse": "LlamaParse 解析"}.get(req.parser, req.parser)
    import uuid as _uuid
    job_id = _uuid.uuid4().hex[:12]
    job_service.jobs_append({
        "doc_id": doc_id,
        "filename": doc.filename,
        "status": "queued",
        "job_type": f"parse_{req.parser}",
        "stage": parser_label,
        "job_id": job_id,
    })
    await job_service.enqueue_parse_jobs([{
        "doc_id": doc_id,
        "filename": doc.filename,
        "file_path": doc.file_path,
        "parser": req.parser,
        "job_id": job_id,
    }])
    return {"id": doc_id, "parser": req.parser, "queued": True, "job_id": job_id}


class ReindexRequest(BaseModel):
    parser: str = "auto"


@router.post("/api/documents/{doc_id}/reindex")
async def reindex_document(doc_id: int, req: ReindexRequest = ReindexRequest(), db: Session = Depends(get_db)):
    doc = db.query(Document).filter(Document.id == doc_id, Document.deleted_at.is_(None)).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    if doc.status == "processing":
        raise HTTPException(status_code=400, detail="Document is already being processed")
    if doc.status not in ("ready", "error"):
        raise HTTPException(status_code=400, detail=f"Cannot reindex document with status: {doc.status}")

    if job_service.jobs_has_active(doc_id):
        return {"id": doc_id, "queued": True}

    tmp_path: str | None = None
    if storage_service._USE_BLOB:
        try:
            tmp_path = await asyncio.to_thread(storage_service.download_blob_to_tmp, doc.file_path)
            file_path = tmp_path
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"無法從 Blob 取得檔案：{e}")
    else:
        file_path = doc.file_path
        if not os.path.exists(file_path):
            raise HTTPException(status_code=404, detail="原始檔案不存在於磁碟，請重新上傳")

    doc.status = "processing"
    db.commit()

    job_service.jobs_enqueue([(doc_id, doc.filename)], job_type="reindex")
    await job_service.enqueue_reindex_job({"doc_id": doc_id, "file_path": file_path, "tmp_path": tmp_path, "parser": req.parser})
    await job_service.push_jobs()

    return {"id": doc_id, "filename": doc.filename, "queued": True}


@router.delete("/api/documents/{doc_id}")
def delete_document(doc_id: int, db: Session = Depends(get_db)):
    doc = db.query(Document).filter(Document.id == doc_id, Document.deleted_at.is_(None)).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    try:
        delete_document_vectors(doc_id)
    except Exception:
        pass

    if storage_service._USE_BLOB:
        storage_service.delete_blob(doc.file_path)
    elif os.path.exists(doc.file_path):
        os.remove(doc.file_path)

    doc.status = "deleted"
    doc.deleted_at = datetime.datetime.utcnow()
    db.commit()
    return {"ok": True}


@router.get("/api/documents/{doc_id}/chunks")
def get_document_chunks(doc_id: int, db: Session = Depends(get_db)):
    doc = db.query(Document).filter(Document.id == doc_id, Document.deleted_at.is_(None)).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    client = get_qdrant_client()
    results, _ = client.scroll(
        collection_name=COLLECTION_NAME,
        scroll_filter=Filter(must=[FieldCondition(
            key="metadata.document_id",
            match=MatchValue(value=str(doc_id)),
        )]),
        limit=500,
        with_payload=True,
        with_vectors=False,
    )
    chunks = []
    for r in results:
        if not r.payload:
            continue
        meta = r.payload.get("metadata", {})
        page = meta.get("page", "?")
        page_end = meta.get("page_end", page)
        page_num = int(page) + 1 if page != "?" else "?"
        page_end_num = int(page_end) + 1 if page_end != "?" else page_num
        content = r.payload.get("page_content", "")
        chunks.append({
            "id": str(r.id),
            "section": meta.get("section", "unknown"),
            "page": page_num,
            "page_end": page_end_num,
            "chunk_index": meta.get("chunk_index", None),
            "parser": meta.get("parser", ""),
            "chars": len(content),
            "preview": content[:300],
            "content": content,
        })
    chunks.sort(key=lambda c: (c["chunk_index"] if c["chunk_index"] is not None else 9999, c["page"], c["id"]))
    return {"doc_id": doc_id, "filename": doc.filename, "total": len(chunks), "chunks": chunks}


class ChunkUpdateRequest(BaseModel):
    content: str


@router.patch("/api/documents/{doc_id}/chunks/{chunk_id}")
def update_chunk(doc_id: int, chunk_id: str, req: ChunkUpdateRequest, db: Session = Depends(get_db)):
    doc = db.query(Document).filter(Document.id == doc_id, Document.deleted_at.is_(None)).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    client = get_qdrant_client()
    from qdrant_client.models import SetPayload
    client.set_payload(
        collection_name=COLLECTION_NAME,
        payload={"page_content": req.content},
        points=[chunk_id],
    )
    doc.needs_reindex = True
    db.commit()
    return {"updated": chunk_id, "needs_reindex": True}


@router.delete("/api/documents/{doc_id}/chunks/{chunk_id}")
def delete_chunk(doc_id: int, chunk_id: str, db: Session = Depends(get_db)):
    doc = db.query(Document).filter(Document.id == doc_id, Document.deleted_at.is_(None)).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    client = get_qdrant_client()
    from qdrant_client.models import PointIdsList
    client.delete(
        collection_name=COLLECTION_NAME,
        points_selector=PointIdsList(points=[chunk_id]),
    )
    return {"deleted": chunk_id}


@router.get("/api/documents/{doc_id}/export")
def export_document(doc_id: int, db: Session = Depends(get_db)):
    import io
    import json as _json
    import re
    import zipfile
    from datetime import datetime, timezone
    from db import Trace

    doc = db.query(Document).filter(Document.id == doc_id, Document.deleted_at.is_(None)).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    summary = json.loads(doc.summary_json) if doc.summary_json else None
    metadata = {
        "id": doc.id,
        "filename": doc.filename,
        "department": doc.department_hint or "其他",
        "status": doc.status,
        "quality_issue": doc.quality_issue,
        "abstract": doc.abstract_text,
        "summary": summary,
        "created_at": doc.created_at.isoformat() if doc.created_at else None,
        "exported_at": datetime.now(timezone.utc).isoformat(),
    }

    client = get_qdrant_client()
    results, _ = client.scroll(
        collection_name=COLLECTION_NAME,
        scroll_filter=Filter(must=[FieldCondition(
            key="metadata.document_id",
            match=MatchValue(value=str(doc_id)),
        )]),
        limit=2000,
        with_payload=True,
        with_vectors=False,
    )
    chunks = []
    for r in results:
        if not r.payload:
            continue
        meta = r.payload.get("metadata", {})
        page = meta.get("page", "?")
        page_end = meta.get("page_end", page)
        page_num = int(page) + 1 if page != "?" else "?"
        page_end_num = int(page_end) + 1 if page_end != "?" else page_num
        content = r.payload.get("page_content", "")
        chunks.append({
            "id": str(r.id),
            "section": meta.get("section", "unknown"),
            "page": page_num,
            "page_end": page_end_num,
            "chunk_index": meta.get("chunk_index", None),
            "chars": len(content),
            "content": content,
        })
    chunks.sort(key=lambda c: (c["chunk_index"] if c["chunk_index"] is not None else 9999, c["page"] if isinstance(c["page"], int) else 9999, c["id"]))
    chunks_data = {"doc_id": doc_id, "filename": doc.filename, "total": len(chunks), "chunks": chunks}

    traces_raw = []
    if not traces_raw:
        candidates = (
            db.query(Trace)
            .filter(Trace.agent_name != "router_agent", Trace.document_ids.isnot(None), Trace.display.isnot(None))
            .order_by(Trace.start_time.desc()).limit(200).all()
        )
        for t in candidates:
            try:
                if doc_id in json.loads(t.document_ids):
                    traces_raw.append(t)
                    if len(traces_raw) >= 5:
                        break
            except Exception:
                pass

    traces_data = []
    for t in traces_raw:
        latency = None
        if t.start_time and t.end_time:
            latency = round((t.end_time - t.start_time).total_seconds(), 2)
        display = None
        if t.display:
            try:
                display = json.loads(t.display)
            except Exception:
                pass
        traces_data.append({
            "id": t.run_id,
            "name": t.name,
            "status": "error" if t.error else "success",
            "start_time": t.start_time.isoformat() if t.start_time else None,
            "latency_seconds": latency,
            "error": t.error,
            "display": display,
        })

    from starlette.background import BackgroundTask

    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".zip")
    os.close(tmp_fd)
    try:
        with zipfile.ZipFile(tmp_path, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("metadata.json", json.dumps(metadata, ensure_ascii=False, indent=2))
            zf.writestr("chunks.json",   json.dumps(chunks_data, ensure_ascii=False, indent=2))
            zf.writestr("traces.json",   json.dumps(traces_data, ensure_ascii=False, indent=2))

            # Parser cache files — stream from disk, no full read into memory
            cache_files = {
                "cache/llamaparse.md":       _llamaparse_cache_path(doc_id),
                "cache/llamaparse.raw.json": _llamaparse_raw_cache_path(doc_id),
                "cache/azure_di.md":         _azure_di_cache_path(doc_id),
                "cache/pymupdf.md":          _pymupdf_cache_path(doc_id),
            }
            for zip_name, disk_path in cache_files.items():
                try:
                    if os.path.exists(disk_path):
                        zf.write(disk_path, arcname=zip_name)
                except Exception:
                    pass

            try:
                if storage_service._USE_BLOB:
                    blob_tmp = storage_service.download_blob_to_tmp(doc.file_path)
                    try:
                        zf.write(blob_tmp, arcname=doc.filename)
                    finally:
                        os.unlink(blob_tmp)
                else:
                    if os.path.exists(doc.file_path):
                        zf.write(doc.file_path, arcname=doc.filename)
            except Exception:
                pass
    except Exception:
        os.unlink(tmp_path)
        raise

    safe_name = re.sub(r'[^\w\u4e00-\u9fff\-.]', '_', doc.filename.replace('.pdf', ''))
    zip_filename = f"{safe_name}_export.zip"
    return FileResponse(
        tmp_path,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(zip_filename)}"},
        background=BackgroundTask(os.unlink, tmp_path),
    )


@router.patch("/api/documents/{doc_id}/abstract")
def update_abstract(doc_id: int, body: dict, db: Session = Depends(get_db)):
    doc = db.query(Document).filter(Document.id == doc_id, Document.deleted_at.is_(None)).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    doc.abstract_text = body.get("abstract_text") or None
    doc.abstract_edited = True
    db.commit()
    return {"abstract_text": doc.abstract_text}


@router.post("/api/documents/{doc_id}/refresh-abstract")
async def refresh_abstract(doc_id: int, db: Session = Depends(get_db)):
    from rag import extract_abstract, _detect_language
    from langchain_community.document_loaders import PyMuPDFLoader

    doc = db.query(Document).filter(
        Document.id == doc_id,
        Document.status == "ready",
        Document.deleted_at.is_(None),
    ).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found or not ready")

    tmp_path: str | None = None
    if storage_service._USE_BLOB:
        try:
            data = storage_service.read_blob(doc.file_path)
            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as f:
                f.write(data)
                tmp_path = f.name
            file_path = tmp_path
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"無法從 Blob 取得檔案：{e}")
    else:
        file_path = doc.file_path
        if not os.path.exists(file_path):
            raise HTTPException(status_code=404, detail="原始檔案不存在")

    if doc.abstract_edited:
        return {"abstract_text": doc.abstract_text, "skipped": True}

    try:
        docs = await asyncio.to_thread(lambda: PyMuPDFLoader(file_path).load())
        lang = _detect_language(docs)
        abstract = extract_abstract(docs, lang)
        doc.abstract_text = abstract
        db.commit()
        return {"abstract_text": abstract, "skipped": False}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if storage_service._USE_BLOB and tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)


@router.get("/api/documents/{doc_id}/parser-caches")
def get_parser_caches(doc_id: int, db: Session = Depends(get_db)):
    from rag import _pymupdf_cache_path, _llamaparse_cache_path, _azure_di_cache_path
    doc = db.query(Document).filter(Document.id == doc_id, Document.deleted_at.is_(None)).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    parsers = [
        ("pymupdf4llm", _pymupdf_cache_path(doc_id)),
        ("azure_di",    _azure_di_cache_path(doc_id)),
        ("llamaparse",  _llamaparse_cache_path(doc_id)),
    ]
    result = []
    for name, path in parsers:
        exists = os.path.exists(path)
        result.append({"parser": name, "available": exists, "size": os.path.getsize(path) if exists else 0})
    return result


@router.get("/api/documents/{doc_id}/parser-cache/{parser}")
def get_parser_cache_content(doc_id: int, parser: str, db: Session = Depends(get_db)):
    from rag import _pymupdf_cache_path, _llamaparse_cache_path, _azure_di_cache_path, _split_cache_pages
    if parser not in ("pymupdf4llm", "azure_di", "llamaparse"):
        raise HTTPException(status_code=400, detail="Invalid parser")

    doc = db.query(Document).filter(Document.id == doc_id, Document.deleted_at.is_(None)).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    path_fn = {"pymupdf4llm": _pymupdf_cache_path, "azure_di": _azure_di_cache_path, "llamaparse": _llamaparse_cache_path}
    cache_path = path_fn[parser](doc_id)

    if not os.path.exists(cache_path):
        raise HTTPException(status_code=404, detail="Cache not found")

    with open(cache_path, "r", encoding="utf-8") as f:
        content = f.read()

    pages = _split_cache_pages(content)
    return {"parser": parser, "page_count": len(pages), "pages": pages}
