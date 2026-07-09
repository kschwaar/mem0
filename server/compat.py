import json
import uuid
from collections.abc import Iterable
from datetime import datetime
from typing import Any, Dict, Optional

from db import SessionLocal
from fastapi import HTTPException
from models import Event
from sqlalchemy import desc, select

RESERVED_PAYLOAD_KEYS = {
    "data",
    "user_id",
    "agent_id",
    "app_id",
    "run_id",
    "hash",
    "created_at",
    "updated_at",
    "expiration_date",
}


def normalize_filters(filters: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    return dict(filters or {})


def add_app_id_to_metadata(params: Dict[str, Any]) -> Dict[str, Any]:
    params = dict(params)
    app_id = params.pop("app_id", None)
    if app_id:
        metadata = dict(params.get("metadata") or {})
        metadata["app_id"] = app_id
        params["metadata"] = metadata
    return params


def extract_entity_filters(filters: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    found: Dict[str, Any] = {}

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key in {"user_id", "agent_id", "app_id", "run_id"} and not isinstance(item, dict):
                    found[key] = item
                elif key in {"AND", "OR", "NOT"}:
                    visit(item)
                elif key == "metadata" and isinstance(item, dict):
                    for metadata_key, metadata_value in item.items():
                        if metadata_key == "app_id" and not isinstance(metadata_value, dict):
                            found["app_id"] = metadata_value
                else:
                    visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(filters or {})
    return found


def normalize_platform_filters(filters: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Map hosted Platform app_id filters to the OSS metadata-backed shape."""
    if not isinstance(filters, dict):
        return {}
    normalized: Dict[str, Any] = {}
    for key, value in filters.items():
        if key in {"AND", "OR", "NOT"} and isinstance(value, list):
            normalized[key] = [normalize_platform_filters(item) if isinstance(item, dict) else item for item in value]
        elif key == "app_id":
            normalized["metadata"] = {"app_id": value}
        else:
            normalized[key] = value
    return normalized


def _row_payload(row: Any) -> Dict[str, Any]:
    return getattr(row, "payload", None) or {}


def serialize_memory(row: Any) -> Dict[str, Any]:
    payload = _row_payload(row)
    app_id = payload.get("app_id")
    if app_id is None:
        app_id = payload.get("metadata", {}).get("app_id") if isinstance(payload.get("metadata"), dict) else None
    return {
        "id": getattr(row, "id", None),
        "memory": payload.get("data"),
        "user_id": payload.get("user_id"),
        "agent_id": payload.get("agent_id"),
        "app_id": app_id,
        "run_id": payload.get("run_id"),
        "hash": payload.get("hash"),
        "expiration_date": payload.get("expiration_date"),
        "metadata": {k: v for k, v in payload.items() if k not in RESERVED_PAYLOAD_KEYS},
        "created_at": payload.get("created_at"),
        "updated_at": payload.get("updated_at"),
    }


def flatten_memory_response(response: Any) -> list[dict[str, Any]]:
    if isinstance(response, list):
        return [item for item in response if isinstance(item, dict)]
    if isinstance(response, dict):
        items = response.get("results") or response.get("memories") or []
        if isinstance(items, list):
            return [item for item in items if isinstance(item, dict)]
    return []


def event_to_dict(event: Event) -> Dict[str, Any]:
    try:
        data = json.loads(event.data or "{}")
    except json.JSONDecodeError:
        data = {}
    return {
        "id": str(event.id),
        "event_id": str(event.id),
        "event": event.event,
        "status": event.status,
        "source": event.source,
        "user_id": event.user_id,
        "agent_id": event.agent_id,
        "app_id": event.app_id,
        "run_id": event.run_id,
        "data": data,
        "error": event.error,
        "created_at": event.created_at.isoformat() if isinstance(event.created_at, datetime) else event.created_at,
        "updated_at": event.updated_at.isoformat() if isinstance(event.updated_at, datetime) else event.updated_at,
    }


def create_event(
    *,
    event: str,
    status: str = "SUCCEEDED",
    source: str | None = None,
    data: Dict[str, Any] | None = None,
    user_id: str | None = None,
    agent_id: str | None = None,
    app_id: str | None = None,
    run_id: str | None = None,
    error: str | None = None,
) -> Dict[str, Any]:
    with SessionLocal() as session:
        row = Event(
            event=event,
            status=status,
            source=source,
            user_id=user_id,
            agent_id=agent_id,
            app_id=app_id,
            run_id=run_id,
            data=json.dumps(data or {}),
            error=error,
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        return event_to_dict(row)


def list_events(limit: int = 100) -> list[Dict[str, Any]]:
    with SessionLocal() as session:
        rows = session.scalars(select(Event).order_by(desc(Event.created_at)).limit(limit)).all()
        return [event_to_dict(row) for row in rows]


def get_event(event_id: str) -> Dict[str, Any]:
    try:
        parsed_id = uuid.UUID(event_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Event not found.")
    with SessionLocal() as session:
        row = session.get(Event, parsed_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Event not found.")
        return event_to_dict(row)


def iter_rows_from_vector_store(memory: Any, limit: int) -> Iterable[Any]:
    results = memory.vector_store.list(top_k=limit)
    rows = results[0] if results and isinstance(results, list) and isinstance(results[0], list) else results or []
    return rows
