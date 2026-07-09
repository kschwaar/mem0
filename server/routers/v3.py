import logging
from typing import Any, Optional

from auth import verify_auth
from fastapi import APIRouter, Depends, HTTPException, Query
from mem0.exceptions import ValidationError as Mem0ValidationError
from pydantic import BaseModel
from server_state import get_memory_instance

router = APIRouter(prefix="/v3", tags=["v3"])

logger = logging.getLogger(__name__)

_ENTITY_KEYS = ("user_id", "agent_id", "run_id")


def translate_v3_filters(filters: Optional[dict[str, Any]]) -> dict[str, Any]:
    """Convert the v3 (Platform API) filter shape into the flat, top-level-entity-key
    dict that ``Memory.search()``/``Memory.get_all()`` require.

    Both native methods reject filters unless one of user_id/agent_id/run_id
    appears as a literal top-level key -- an ``{"AND": [{"user_id": "x"}, ...]}``
    wrapper (the shape every v3 client sends) does not satisfy that check even
    though the underlying vector store's filter compiler supports AND/OR/NOT
    natively. So AND conditions get flattened onto top-level keys here.

    ``app_id`` is dropped entirely: self-hosted ``Memory.add()`` has no
    ``app_id`` parameter, so it is never present in a stored payload -- filtering
    on it would silently match nothing rather than error, exactly the
    truthy-gated-filter trap already fixed for ``agent_id`` in the MCP tools.
    ``metadata.<key>`` sub-conditions are promoted to a top-level scalar filter
    since metadata values *are* stored flat in the payload.
    """
    if not filters:
        return {}

    if "AND" not in filters and "OR" not in filters:
        return {k: v for k, v in filters.items() if k != "app_id"}

    if "AND" in filters:
        result: dict[str, Any] = {}
        for condition in filters["AND"]:
            for key, value in condition.items():
                if key == "app_id":
                    continue
                if key in _ENTITY_KEYS and isinstance(value, str):
                    result[key] = value
                elif key == "metadata" and isinstance(value, dict):
                    for meta_key, meta_value in value.items():
                        if isinstance(meta_value, str):
                            result[meta_key] = meta_value
        return result

    logger.warning("v3 OR filter not supported on self-hosted server, ignoring: %r", filters)
    return {}


class V3AddRequest(BaseModel):
    messages: Optional[list[dict]] = None
    text: Optional[str] = None
    user_id: Optional[str] = None
    agent_id: Optional[str] = None
    run_id: Optional[str] = None
    app_id: Optional[str] = None
    metadata: Optional[dict] = None
    infer: bool = True
    source: Optional[str] = None


class V3ListRequest(BaseModel):
    filters: Optional[dict[str, Any]] = None
    source: Optional[str] = None


class V3SearchRequest(BaseModel):
    query: str
    top_k: int = 10
    threshold: float = 0.3
    filters: Optional[dict[str, Any]] = None
    rerank: bool = False
    source: Optional[str] = None


@router.post("/memories/add/")
def v3_add_memory(req: V3AddRequest, _auth=Depends(verify_auth)):
    if not any([req.user_id, req.agent_id, req.run_id]):
        raise HTTPException(status_code=400, detail="At least one of user_id, agent_id, run_id is required.")

    metadata = dict(req.metadata or {})
    if req.app_id:
        metadata["app_id"] = req.app_id

    params: dict[str, Any] = {}
    if req.user_id:
        params["user_id"] = req.user_id
    if req.agent_id:
        params["agent_id"] = req.agent_id
    if req.run_id:
        params["run_id"] = req.run_id
    if metadata:
        params["metadata"] = metadata
    if not req.infer:
        params["infer"] = False

    messages = req.messages or [{"role": "user", "content": req.text}]

    try:
        return get_memory_instance().add(messages, **params)
    except (ValueError, Mem0ValidationError) as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/memories/")
def v3_list_memories(
    req: V3ListRequest,
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=1000),
    _auth=Depends(verify_auth),
):
    filters = translate_v3_filters(req.filters)
    if not filters:
        raise HTTPException(status_code=400, detail="filters are required for listing memories.")

    try:
        result = get_memory_instance().get_all(filters=filters, top_k=page * page_size)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    items = result.get("results", []) if isinstance(result, dict) else (result or [])

    start = (page - 1) * page_size
    end = start + page_size
    return {
        "results": items[start:end],
        "count": len(items),
        "page": page,
        "page_size": page_size,
    }


@router.post("/memories/search/")
def v3_search_memories(req: V3SearchRequest, _auth=Depends(verify_auth)):
    filters = translate_v3_filters(req.filters)

    params: dict[str, Any] = {"top_k": req.top_k, "threshold": req.threshold}
    if filters:
        params["filters"] = filters
    if req.rerank:
        params["rerank"] = True

    try:
        return get_memory_instance().search(query=req.query, **params)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
