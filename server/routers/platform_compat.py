import contextvars
import json
import os
from typing import Any, Dict, List, Optional

import anyio
import server_state
from auth import require_admin, verify_auth
from compat import (
    add_app_id_to_metadata,
    create_event,
    extract_entity_filters,
    flatten_memory_response,
    get_event,
    list_events,
    normalize_filters,
    normalize_platform_filters,
)
from errors import upstream_error
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response
from mem0.exceptions import ValidationError as Mem0ValidationError
from pydantic import BaseModel, Field
from schemas import MessageResponse

try:
    from mcp.server.fastmcp import FastMCP
    from mcp.server.streamable_http import StreamableHTTPServerTransport
except ImportError:  # pragma: no cover - exercised only when server deps are not installed
    FastMCP = None
    StreamableHTTPServerTransport = None

router = APIRouter(tags=["platform-compat"])

ALL_MEMORIES_LIMIT = 1000
DEFAULT_MCP_AGENT_ID = "claude-code"

mcp_user_id_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("mcp_user_id", default=None)
mcp_agent_id_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("mcp_agent_id", default=None)
mcp_app_id_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("mcp_app_id", default=None)

mcp = FastMCP("mem0-self-hosted") if FastMCP is not None else None


class Message(BaseModel):
    role: str
    content: str


class PlatformMemoryAdd(BaseModel):
    messages: List[Message]
    user_id: Optional[str] = None
    agent_id: Optional[str] = None
    app_id: Optional[str] = None
    run_id: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    expiration_date: Optional[str] = None
    infer: Optional[bool] = None
    memory_type: Optional[str] = None
    prompt: Optional[str] = None
    immutable: Optional[bool] = None
    categories: Optional[list[str]] = None
    source: Optional[str] = None


class PlatformMemorySearch(BaseModel):
    query: str
    filters: Optional[Dict[str, Any]] = None
    top_k: Optional[int] = Field(default=10, ge=0, le=ALL_MEMORIES_LIMIT)
    threshold: Optional[float] = None
    rerank: Optional[bool] = None
    keyword_search: Optional[bool] = None
    fields: Optional[list[str]] = None
    source: Optional[str] = None


class PlatformMemoryList(BaseModel):
    filters: Optional[Dict[str, Any]] = None
    source: Optional[str] = None


class PlatformMemoryUpdate(BaseModel):
    text: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    expiration_date: Optional[str] = None
    source: Optional[str] = None


def _client_error(exc: Exception) -> HTTPException:
    detail = str(exc)
    status_code = 404 if isinstance(exc, ValueError) and "not found" in detail.lower() else 400
    return HTTPException(status_code=status_code, detail=detail)


def _memory():
    return server_state.get_memory_instance()


def _delete_by_filters(filters: Dict[str, Any]) -> int:
    memory = _memory()
    entity_filters = {k: v for k, v in extract_entity_filters(filters).items() if v}
    if not entity_filters:
        raise HTTPException(status_code=400, detail="At least one identifier is required.")

    if "app_id" not in entity_filters:
        memory.delete_all(
            user_id=entity_filters.get("user_id"),
            agent_id=entity_filters.get("agent_id"),
            run_id=entity_filters.get("run_id"),
        )
        return 0

    query_filters = normalize_platform_filters(filters)
    if not any(key in query_filters for key in ("user_id", "agent_id", "run_id", "AND", "OR")):
        query_filters["user_id"] = entity_filters.get("user_id", "*")
    listed = memory.get_all(filters=query_filters, top_k=ALL_MEMORIES_LIMIT, show_expired=True)
    deleted = 0
    for item in flatten_memory_response(listed):
        memory_id = item.get("id")
        if memory_id:
            memory.delete(memory_id=memory_id)
            deleted += 1
    return deleted


@router.get("/v1/ping/")
def ping(user=Depends(verify_auth)):
    return {
        "status": "ok",
        "connected": True,
        "user_email": getattr(user, "email", None) if user is not None else None,
        "server": "mem0-self-hosted",
    }


@router.post("/v3/memories/add/")
def add_memory(req: PlatformMemoryAdd, _auth=Depends(verify_auth)):
    if not any([req.user_id, req.agent_id, req.run_id]):
        raise HTTPException(status_code=400, detail="At least one identifier (user_id, agent_id, run_id) is required.")
    params = {
        k: v
        for k, v in req.model_dump().items()
        if v is not None and k not in {"messages", "immutable", "categories", "source"}
    }
    if req.categories:
        metadata = dict(params.get("metadata") or {})
        metadata["categories"] = req.categories
        params["metadata"] = metadata
    params = add_app_id_to_metadata(params)
    try:
        response = _memory().add(messages=[message.model_dump() for message in req.messages], **params)
        event = create_event(
            event="ADD",
            status="SUCCEEDED",
            source=req.source,
            user_id=req.user_id,
            agent_id=req.agent_id,
            app_id=req.app_id,
            run_id=req.run_id,
            data={"response": response},
        )
        if isinstance(response, dict):
            response = dict(response)
            response.setdefault("status", event["status"])
            response.setdefault("event_id", event["event_id"])
            return response
        return {"results": response, "status": event["status"], "event_id": event["event_id"]}
    except (ValueError, Mem0ValidationError) as e:
        raise _client_error(e)
    except Exception:
        raise upstream_error()


@router.post("/v3/memories/search/")
def search_memories(req: PlatformMemorySearch, _auth=Depends(verify_auth)):
    filters = normalize_platform_filters(normalize_filters(req.filters))
    params: Dict[str, Any] = {}
    if req.top_k is not None:
        params["top_k"] = req.top_k
    if req.threshold is not None:
        params["threshold"] = req.threshold
    if req.rerank is not None:
        params["rerank"] = req.rerank
    try:
        return _memory().search(query=req.query, filters=filters, **params)
    except (ValueError, Mem0ValidationError) as e:
        raise _client_error(e)
    except Exception:
        raise upstream_error()


@router.post("/v3/memories/")
def list_memories(
    req: PlatformMemoryList,
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=0, le=ALL_MEMORIES_LIMIT),
    _auth=Depends(verify_auth),
):
    filters = normalize_platform_filters(normalize_filters(req.filters))
    try:
        response = _memory().get_all(
            filters=filters, top_k=min(page * page_size, ALL_MEMORIES_LIMIT), show_expired=True
        )
        results = flatten_memory_response(response)
        start = (page - 1) * page_size
        end = start + page_size
        return {"results": results[start:end], "count": len(results), "page": page, "page_size": page_size}
    except (ValueError, Mem0ValidationError) as e:
        raise _client_error(e)
    except Exception:
        raise upstream_error()


@router.get("/v1/memories/{memory_id}/")
def get_memory(memory_id: str, _auth=Depends(verify_auth)):
    try:
        return _memory().get(memory_id)
    except (ValueError, Mem0ValidationError) as e:
        raise _client_error(e)
    except Exception:
        raise upstream_error()


@router.put("/v1/memories/{memory_id}/")
def update_memory(memory_id: str, req: PlatformMemoryUpdate, _auth=Depends(verify_auth)):
    try:
        fields_set = getattr(req, "model_fields_set", getattr(req, "__fields_set__", set()))
        params: Dict[str, Any] = {"memory_id": memory_id}
        if "text" in fields_set:
            params["text"] = req.text
        if "metadata" in fields_set:
            params["metadata"] = req.metadata
        if "expiration_date" in fields_set:
            params["expiration_date"] = req.expiration_date
        return _memory().update(**params)
    except (ValueError, Mem0ValidationError) as e:
        raise _client_error(e)
    except Exception:
        raise upstream_error()


@router.delete("/v1/memories/{memory_id}/", response_model=MessageResponse)
def delete_memory(memory_id: str, _auth=Depends(verify_auth)):
    try:
        _memory().delete(memory_id=memory_id)
        return MessageResponse(message="Memory deleted successfully")
    except (ValueError, Mem0ValidationError) as e:
        raise _client_error(e)
    except Exception:
        raise upstream_error()


@router.get("/v1/memories/{memory_id}/history/")
def memory_history(memory_id: str, _auth=Depends(verify_auth)):
    try:
        return _memory().history(memory_id=memory_id)
    except Exception:
        raise upstream_error()


@router.delete("/v1/memories/", response_model=MessageResponse)
def delete_all_memories(
    user_id: Optional[str] = None,
    agent_id: Optional[str] = None,
    app_id: Optional[str] = None,
    run_id: Optional[str] = None,
    _auth=Depends(require_admin),
):
    filters = {
        k: v for k, v in {"user_id": user_id, "agent_id": agent_id, "app_id": app_id, "run_id": run_id}.items() if v
    }
    try:
        _delete_by_filters(filters)
        return MessageResponse(message="All relevant memories deleted")
    except HTTPException:
        raise
    except Exception:
        raise upstream_error()


@router.get("/v1/entities/")
def list_entities(_auth=Depends(verify_auth)):
    from routers.entities import list_entities as base_list_entities

    return base_list_entities(_auth)


@router.delete("/v2/entities/{entity_type}/{entity_id}/", response_model=MessageResponse)
def delete_entity(entity_type: str, entity_id: str, _auth=Depends(require_admin)):
    if entity_type not in {"user", "agent", "app", "run"}:
        raise HTTPException(status_code=422, detail="Unsupported entity type.")
    field = "app_id" if entity_type == "app" else f"{entity_type}_id"
    try:
        _delete_by_filters({field: entity_id})
        return MessageResponse(message="Entity deleted")
    except HTTPException:
        raise
    except Exception:
        raise upstream_error()


@router.get("/v1/events/")
def events(limit: int = Query(100, ge=1, le=ALL_MEMORIES_LIMIT), _auth=Depends(verify_auth)):
    return {"results": list_events(limit=limit)}


@router.get("/v1/event/{event_id}/")
def event_status(event_id: str, _auth=Depends(verify_auth)):
    return get_event(event_id)


MCP_TOOLS = {
    "add_memory": "Add a memory.",
    "search_memories": "Search memories.",
    "get_memories": "List memories.",
    "get_memory": "Get a memory by ID.",
    "update_memory": "Update a memory.",
    "delete_memory": "Delete a memory.",
    "delete_all_memories": "Delete scoped memories.",
    "delete_entities": "Delete a user, agent, app, or run entity.",
    "list_entities": "List stored entities.",
    "list_events": "List processing events.",
    "get_event_status": "Get processing event status.",
}


def _resolve_mcp_identity(request: Request) -> tuple[Optional[str], Optional[str], Optional[str]]:
    user_id = request.headers.get("X-User-ID") or os.getenv("MEM0_MCP_USER_ID")
    agent_id = request.headers.get("X-Agent-ID") or os.getenv("MEM0_MCP_AGENT_ID", DEFAULT_MCP_AGENT_ID)
    app_id = request.headers.get("X-App-ID") or request.headers.get("X-Project-ID") or os.getenv("MEM0_MCP_APP_ID")
    return user_id, agent_id, app_id


def _entity_defaults() -> Dict[str, str]:
    defaults = {}
    if mcp_user_id_var.get():
        defaults["user_id"] = mcp_user_id_var.get()
    if mcp_agent_id_var.get():
        defaults["agent_id"] = mcp_agent_id_var.get()
    if mcp_app_id_var.get():
        defaults["app_id"] = mcp_app_id_var.get()
    return defaults


def _merge_filter_default(filters: Optional[Dict[str, Any]], key: str, value: Optional[str]) -> Dict[str, Any]:
    if not value:
        return dict(filters or {})
    merged = dict(filters or {})
    if key == "app_id":
        if "app_id" not in merged and "metadata" not in merged:
            merged["app_id"] = value
        return merged
    if key not in merged and not any(operator in merged for operator in ("AND", "OR", "NOT")):
        merged[key] = value
    return merged


def _apply_mcp_identity_defaults(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    payload = dict(args)
    defaults = _entity_defaults()
    if name == "add_memory":
        for key, value in defaults.items():
            payload.setdefault(key, value)
    elif name in {"search_memories", "get_memories"}:
        filters = payload.get("filters")
        for key, value in defaults.items():
            filters = _merge_filter_default(filters, key, value)
        if filters:
            payload["filters"] = filters
    elif name in {"delete_all_memories", "delete_entities"} and not any(
        payload.get(key) for key in ("user_id", "agent_id", "app_id", "run_id")
    ):
        for key, value in defaults.items():
            payload.setdefault(key, value)
    return payload


def _jsonrpc_result(req_id: Any, result: Any) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _jsonrpc_error(req_id: Any, code: int, message: str) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def _tool_schema(name: str, description: str) -> Dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "inputSchema": {"type": "object", "additionalProperties": True, "properties": {}},
    }


def _mcp_content(value: Any) -> Dict[str, Any]:
    return {"content": [{"type": "text", "text": json.dumps(value, default=str)}]}


def _memory_id_arg(args: Dict[str, Any]) -> str:
    memory_id = args.get("memory_id") or args.get("id")
    if not memory_id:
        raise HTTPException(status_code=400, detail="memory_id is required.")
    return memory_id


def _call_tool(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    args = _apply_mcp_identity_defaults(name, args)
    if name == "add_memory":
        payload = dict(args)
        if "messages" not in payload and payload.get("text"):
            payload["messages"] = [{"role": "user", "content": payload.pop("text")}]
        req = PlatformMemoryAdd(**payload)
        return _mcp_content(add_memory(req))
    if name == "search_memories":
        req = PlatformMemorySearch(**args)
        return _mcp_content(search_memories(req))
    if name == "get_memories":
        req = PlatformMemoryList(filters=args.get("filters"))
        return _mcp_content(list_memories(req, page=args.get("page", 1), page_size=args.get("page_size", 100)))
    if name == "get_memory":
        return _mcp_content(get_memory(_memory_id_arg(args)))
    if name == "update_memory":
        req = PlatformMemoryUpdate(
            text=args.get("text"), metadata=args.get("metadata"), expiration_date=args.get("expiration_date")
        )
        return _mcp_content(update_memory(_memory_id_arg(args), req))
    if name == "delete_memory":
        return _mcp_content(delete_memory(_memory_id_arg(args)).model_dump())
    if name == "delete_all_memories":
        return _mcp_content(
            delete_all_memories(
                args.get("user_id"), args.get("agent_id"), args.get("app_id"), args.get("run_id")
            ).model_dump()
        )
    if name == "delete_entities":
        for entity_type, key in (("user", "user_id"), ("agent", "agent_id"), ("app", "app_id"), ("run", "run_id")):
            if args.get(key):
                return _mcp_content(delete_entity(entity_type, args[key]).model_dump())
        raise HTTPException(status_code=400, detail="At least one entity ID is required.")
    if name == "list_entities":
        return _mcp_content(list_entities())
    if name == "list_events":
        return _mcp_content({"results": list_events(limit=args.get("limit", 100))})
    if name == "get_event_status":
        return _mcp_content(get_event(args["event_id"]))
    raise KeyError(name)


def _call_tool_text(name: str, args: Dict[str, Any]) -> str:
    return json.dumps(_call_tool(name, args), default=str)


if mcp is not None:

    @mcp.tool(name="add_memory", description=MCP_TOOLS["add_memory"])
    async def mcp_add_memory(
        text: Optional[str] = None,
        messages: Optional[list[dict[str, Any]]] = None,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        app_id: Optional[str] = None,
        run_id: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
        infer: Optional[bool] = None,
    ) -> str:
        args = {
            "text": text,
            "messages": messages,
            "user_id": user_id,
            "agent_id": agent_id,
            "app_id": app_id,
            "run_id": run_id,
            "metadata": metadata,
            "infer": infer,
        }
        return _call_tool_text("add_memory", {key: value for key, value in args.items() if value is not None})

    @mcp.tool(name="search_memories", description=MCP_TOOLS["search_memories"])
    async def mcp_search_memories(
        query: str,
        filters: Optional[dict[str, Any]] = None,
        top_k: Optional[int] = None,
        limit: Optional[int] = None,
        threshold: Optional[float] = None,
        rerank: Optional[bool] = None,
    ) -> str:
        args = {"query": query, "filters": filters, "top_k": top_k or limit, "threshold": threshold, "rerank": rerank}
        return _call_tool_text("search_memories", {key: value for key, value in args.items() if value is not None})

    @mcp.tool(name="get_memories", description=MCP_TOOLS["get_memories"])
    async def mcp_get_memories(
        filters: Optional[dict[str, Any]] = None,
        page: int = 1,
        page_size: int = 100,
    ) -> str:
        return _call_tool_text("get_memories", {"filters": filters, "page": page, "page_size": page_size})

    @mcp.tool(name="get_memory", description=MCP_TOOLS["get_memory"])
    async def mcp_get_memory(memory_id: str) -> str:
        return _call_tool_text("get_memory", {"memory_id": memory_id})

    @mcp.tool(name="update_memory", description=MCP_TOOLS["update_memory"])
    async def mcp_update_memory(
        memory_id: str,
        text: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
        expiration_date: Optional[str] = None,
    ) -> str:
        args = {"memory_id": memory_id, "text": text, "metadata": metadata, "expiration_date": expiration_date}
        return _call_tool_text("update_memory", {key: value for key, value in args.items() if value is not None})

    @mcp.tool(name="delete_memory", description=MCP_TOOLS["delete_memory"])
    async def mcp_delete_memory(memory_id: str) -> str:
        return _call_tool_text("delete_memory", {"memory_id": memory_id})

    @mcp.tool(name="delete_all_memories", description=MCP_TOOLS["delete_all_memories"])
    async def mcp_delete_all_memories(
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        app_id: Optional[str] = None,
        run_id: Optional[str] = None,
    ) -> str:
        args = {"user_id": user_id, "agent_id": agent_id, "app_id": app_id, "run_id": run_id}
        return _call_tool_text("delete_all_memories", {key: value for key, value in args.items() if value is not None})

    @mcp.tool(name="delete_entities", description=MCP_TOOLS["delete_entities"])
    async def mcp_delete_entities(
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        app_id: Optional[str] = None,
        run_id: Optional[str] = None,
    ) -> str:
        args = {"user_id": user_id, "agent_id": agent_id, "app_id": app_id, "run_id": run_id}
        return _call_tool_text("delete_entities", {key: value for key, value in args.items() if value is not None})

    @mcp.tool(name="list_entities", description=MCP_TOOLS["list_entities"])
    async def mcp_list_entities() -> str:
        return _call_tool_text("list_entities", {})

    @mcp.tool(name="list_events", description=MCP_TOOLS["list_events"])
    async def mcp_list_events(limit: int = 100) -> str:
        return _call_tool_text("list_events", {"limit": limit})

    @mcp.tool(name="get_event_status", description=MCP_TOOLS["get_event_status"])
    async def mcp_get_event_status(event_id: str) -> str:
        return _call_tool_text("get_event_status", {"event_id": event_id})


async def _handle_fastmcp_streamable_http(request: Request) -> Response:
    if StreamableHTTPServerTransport is None or mcp is None:
        raise RuntimeError("FastMCP is not available")

    response_started = False
    response_status = 200
    response_headers: list[tuple[bytes, bytes]] = []
    response_body = bytearray()

    async def capture_send(message):
        nonlocal response_started, response_status
        if message["type"] == "http.response.start":
            response_started = True
            response_status = message["status"]
            response_headers.extend(message.get("headers", []))
        elif message["type"] == "http.response.body":
            response_body.extend(message.get("body", b""))

    transport = StreamableHTTPServerTransport(mcp_session_id=None, is_json_response_enabled=True)
    async with anyio.create_task_group() as tg:

        async def run_server(*, task_status=anyio.TASK_STATUS_IGNORED):
            async with transport.connect() as (read_stream, write_stream):
                task_status.started()
                await mcp._mcp_server.run(
                    read_stream,
                    write_stream,
                    mcp._mcp_server.create_initialization_options(),
                    stateless=True,
                )

        await tg.start(run_server)
        await transport.handle_request(request.scope, request.receive, capture_send)
        await transport.terminate()
        tg.cancel_scope.cancel()

    if not response_started:
        return Response(status_code=500, content=b"Transport did not produce a response")

    return Response(
        content=bytes(response_body),
        status_code=response_status,
        headers={k.decode("latin-1"): v.decode("latin-1") for k, v in response_headers},
    )


async def _handle_legacy_jsonrpc_mcp(request: Request) -> JSONResponse | Dict[str, Any]:
    if request.method == "GET":
        transport = "streamable-http" if mcp is not None else "json-rpc-fallback"
        return JSONResponse({"status": "ok", "transport": transport})
    if request.method == "DELETE":
        return JSONResponse(status_code=202, content={})
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content=_jsonrpc_error(None, -32700, "Parse error"))

    req_id = body.get("id")
    method = body.get("method")
    if method == "initialize":
        return _jsonrpc_result(
            req_id,
            {
                "protocolVersion": "2025-03-26",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "mem0-self-hosted", "version": "1.0.0"},
            },
        )
    if method == "notifications/initialized":
        return JSONResponse(status_code=202, content={})
    if method == "tools/list":
        return _jsonrpc_result(
            req_id, {"tools": [_tool_schema(name, description) for name, description in MCP_TOOLS.items()]}
        )
    if method == "tools/call":
        params = body.get("params") or {}
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if name not in MCP_TOOLS:
            return _jsonrpc_error(req_id, -32602, f"Unknown tool: {name}")
        try:
            return _jsonrpc_result(req_id, _call_tool(name, arguments))
        except HTTPException as exc:
            return _jsonrpc_error(req_id, exc.status_code, str(exc.detail))
        except Exception as exc:
            return _jsonrpc_error(req_id, -32000, str(exc))
    return _jsonrpc_error(req_id, -32601, f"Method not found: {method}")


@router.api_route("/mcp/", methods=["POST", "GET", "DELETE"])
@router.api_route("/mcp", methods=["POST", "GET", "DELETE"])
async def mcp_endpoint(request: Request, _auth=Depends(verify_auth)):
    user_id, agent_id, app_id = _resolve_mcp_identity(request)
    user_token = mcp_user_id_var.set(user_id)
    agent_token = mcp_agent_id_var.set(agent_id)
    app_token = mcp_app_id_var.set(app_id)
    try:
        if mcp is not None and StreamableHTTPServerTransport is not None:
            return await _handle_fastmcp_streamable_http(request)
        return await _handle_legacy_jsonrpc_mcp(request)
    finally:
        mcp_user_id_var.reset(user_token)
        mcp_agent_id_var.reset(agent_token)
        mcp_app_id_var.reset(app_token)
