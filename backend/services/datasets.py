"""Dataset services for repeatable trace evaluations."""
from __future__ import annotations
from utils import new_id

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from db import Dataset, DatasetItem
from services.trace_read.service import TraceReadService


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _json_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def _json_obj(value: Any) -> Any:
    if value is None or isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return value


def _dataset_payload(row: Dataset, *, include_items: bool = False) -> dict:
    items = [item for item in row.items if not item.is_archived and not getattr(item, "is_deleted", False)]
    payload = {
        "dataset_id": row.dataset_id,
        "name": row.name,
        "description": row.description,
        "source": row.source,
        "metadata": _json_obj(row.metadata_json) or {},
        "input_schema": _json_obj(row.input_schema),
        "expected_output_schema": _json_obj(row.expected_output_schema),
        "is_archived": row.is_archived,
        "item_count": len(items),
        "created_at": row.created_at.isoformat() + "Z" if row.created_at else None,
        "updated_at": row.updated_at.isoformat() + "Z" if row.updated_at else None,
    }
    if include_items:
        payload["items"] = [_dataset_item_payload(item) for item in sorted(items, key=lambda item: item.created_at or datetime.min)]
    return payload


def _dataset_item_payload(row: DatasetItem) -> dict:
    return {
        "dataset_item_id": row.dataset_item_id,
        "dataset_id": row.dataset_id,
        "input": _json_obj(row.input),
        "output": _json_obj(row.output),
        "reference_output": _json_obj(row.output),
        "expected_output": _json_obj(row.expected_output),
        "context": _json_obj(row.context),
        "source_trace_id": row.source_trace_id,
        "source_observation_id": row.source_observation_id,
        "status": row.status,
        "tags": _json_obj(row.tags) or [],
        "metadata": _json_obj(row.metadata_json) or {},
        "is_archived": row.is_archived,
        "is_deleted": row.is_deleted,
        "valid_from": row.valid_from.isoformat() + "Z" if row.valid_from else None,
        "valid_to": row.valid_to.isoformat() + "Z" if row.valid_to else None,
        "created_at": row.created_at.isoformat() + "Z" if row.created_at else None,
        "updated_at": row.updated_at.isoformat() + "Z" if row.updated_at else None,
    }


def _answer_from_payload(detail: dict) -> Any:
    outputs = detail.get("outputs_raw")
    if isinstance(outputs, dict):
        return outputs.get("answer") or outputs.get("content") or outputs
    return outputs or detail.get("output")


def _sources_from_payload(detail: dict) -> list:
    outputs = detail.get("outputs_raw")
    if isinstance(outputs, dict) and isinstance(outputs.get("sources"), list):
        return outputs["sources"]
    sources: list = []
    for child in detail.get("children") or []:
        child_output = child.get("outputs_raw") if isinstance(child, dict) else None
        if isinstance(child_output, dict) and isinstance(child_output.get("sources"), list):
            sources.extend(child_output["sources"])
    return sources


class DatasetService:
    @staticmethod
    def create_dataset(
        db: Session,
        *,
        name: str,
        description: str | None = None,
        source: str | None = None,
        metadata: dict | None = None,
        input_schema: dict | None = None,
        expected_output_schema: dict | None = None,
    ) -> Dataset:
        now = _utcnow()
        row = Dataset(
            dataset_id=f"dataset-{new_id()}",
            name=name,
            description=description,
            source=source or "manual",
            metadata_json=_json_text(metadata or {}),
            input_schema=_json_text(input_schema),
            expected_output_schema=_json_text(expected_output_schema),
            created_at=now,
            updated_at=now,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row

    @staticmethod
    def add_trace_item(db: Session, dataset_id: str, trace_id: str, *, tags: list[str] | None = None) -> DatasetItem:
        dataset = db.query(Dataset).filter(Dataset.dataset_id == dataset_id, Dataset.is_archived.is_(False)).first()
        if dataset is None:
            raise HTTPException(status_code=404, detail="Dataset not found")

        existing = (
            db.query(DatasetItem)
            .filter(DatasetItem.dataset_id == dataset_id, DatasetItem.source_trace_id == trace_id)
            .first()
        )
        if existing:
            return existing

        detail = TraceReadService(db).trace_detail(trace_id)
        now = _utcnow()
        context = {
            "trace_id": trace_id,
            "children": detail.get("children") or [],
            "sources": _sources_from_payload(detail),
            "agent_name": detail.get("agent_name"),
            "quality_score": detail.get("quality_score"),
            "quality_detail": detail.get("quality_detail"),
        }
        row = DatasetItem(
            dataset_item_id=f"{dataset_id}:{trace_id}",
            dataset_id=dataset_id,
            input=_json_text(detail.get("inputs_raw") or detail.get("input")),
            output=_json_text(detail.get("outputs_raw") or detail.get("output")),
            expected_output=_json_text(_answer_from_payload(detail)),
            context=_json_text(context),
            source_trace_id=trace_id,
            source_observation_id=None,
            status="active",
            tags=_json_text(tags or [value for value in [detail.get("agent_name")] if value]),
            metadata_json=_json_text({"source": "trace", "trace_id": trace_id}),
            valid_from=now,
            created_at=now,
            updated_at=now,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row

    @staticmethod
    def add_low_quality_traces(
        db: Session,
        dataset_id: str,
        *,
        max_quality: float = 3.0,
        limit: int = 50,
    ) -> dict:
        service = TraceReadService(db)
        traces = service.list_traces(limit=limit, max_quality=max_quality, has_score=True)
        items = []
        for trace in traces:
            items.append(DatasetService.add_trace_item(db, dataset_id, trace["id"], tags=["low_quality"]))
        return {"added": len(items), "items": [_dataset_item_payload(item) for item in items]}

    @staticmethod
    def list_datasets(db: Session, *, limit: int = 50, offset: int = 0, include_archived: bool = False) -> dict:
        q = db.query(Dataset)
        if not include_archived:
            q = q.filter(Dataset.is_archived.is_(False))
        total = q.count()
        rows = q.order_by(Dataset.created_at.desc()).offset(offset).limit(limit).all()
        return {"datasets": [_dataset_payload(row) for row in rows], "total": total}

    @staticmethod
    def get_dataset_detail(db: Session, dataset_id: str) -> dict:
        row = db.query(Dataset).filter(Dataset.dataset_id == dataset_id).first()
        if row is None or row.is_archived:
            raise HTTPException(status_code=404, detail="Dataset not found")
        return _dataset_payload(row, include_items=True)

    @staticmethod
    def get_item(db: Session, dataset_item_id: str) -> DatasetItem | None:
        return (
            db.query(DatasetItem)
            .filter(
                DatasetItem.dataset_item_id == dataset_item_id,
                DatasetItem.is_archived.is_(False),
                DatasetItem.is_deleted.is_(False),
            )
            .first()
        )

    @staticmethod
    def add_manual_item(
        db: Session,
        dataset_id: str,
        *,
        input: dict | None = None,
        output: dict | str | None = None,
        expected_output: dict | str | None = None,
        context: dict | None = None,
        tags: list[str] | None = None,
        metadata: dict | None = None,
    ) -> DatasetItem:
        dataset = db.query(Dataset).filter(Dataset.dataset_id == dataset_id, Dataset.is_archived.is_(False)).first()
        if dataset is None:
            raise HTTPException(status_code=404, detail="Dataset not found")

        now = _utcnow()
        row = DatasetItem(
            dataset_item_id=f"item-{new_id()}",
            dataset_id=dataset_id,
            input=_json_text(input),
            output=_json_text(output),
            expected_output=_json_text(expected_output),
            context=_json_text(context),
            tags=_json_text(tags or []),
            metadata_json=_json_text({"source": "manual", **(metadata or {})}),
            status="active",
            valid_from=now,
            created_at=now,
            updated_at=now,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row

    @staticmethod
    def archive_item(db: Session, dataset_id: str, dataset_item_id: str) -> DatasetItem:
        row = (
            db.query(DatasetItem)
            .filter(DatasetItem.dataset_id == dataset_id, DatasetItem.dataset_item_id == dataset_item_id)
            .first()
        )
        if row is None:
            raise HTTPException(status_code=404, detail="Item not found")
        now = _utcnow()
        row.is_archived = True
        row.is_deleted = True
        row.status = "deleted"
        row.valid_to = row.valid_to or now
        row.updated_at = now
        db.commit()
        db.refresh(row)
        return row
