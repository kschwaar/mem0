import contextvars
import json
import logging
import os
from typing import Optional

import anyio
from auth import verify_auth
from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from mcp.server.fastmcp import FastMCP
from mcp.server.streamable_http import StreamableHTTPServerTransport
from routers.entities import TYPE_TO_FIELD, _iter_payloads, _parse_timestamp
from server_state import get_memory_instance

logger = logging.getLogger(__name__)

mcp = FastMCP("mem0-mcp-server")
mcp_router = APIRouter(prefix="/mcp", tags=["mcp"])

# Context variables set per-request from headers; reset in handle_streamable_http's
# finally block so concurrent requests on this process don't leak identity into each other.
user_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("user_id")
agent_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("agent_id")


def _resolve_identity(request: Request) -> tuple[str, str]:
    user_id = request.headers.get("X-User-ID") or os.getenv("MEM0_MCP_USER_ID")
    agent_id = request.headers.get("X-Agent-ID") or os.getenv("MEM0_MCP_AGENT_ID", "claude-code")
    if not user_id:
        raise ValueError("user_id is required. Pass X-User-ID header or set MEM0_MCP_USER_ID env var.")
    return user_id, agent_id


@mcp.tool(description="Store a new memory.")
async def add_memory(text: str, infer: bool = True, metadata: Optional[dict] = None) -> str:
    user_id = user_id_var.get(None)
    agent_id = agent_id_var.get(None)
    if not user_id:
        return "Error: user_id not set"
    meta = dict(metadata or {})
    if agent_id:
        meta["agent_id"] = agent_id
    try:
        result = get_memory_instance().add(text, user_id=user_id, infer=infer, metadata=meta)
        return json.dumps(result)
    except Exception as e:
        logger.exception("Error adding memory")
        return f"Error: {e}"


@mcp.tool(description="Search memories. Call this on every user message.")
async def search_memories(query: str, limit: int = 10) -> str:
    user_id = user_id_var.get(None)
    if not user_id:
        return "Error: user_id not set"
    try:
        result = get_memory_instance().search(query=query, filters={"user_id": user_id}, top_k=limit)
        return json.dumps(result)
    except Exception as e:
        logger.exception("Error searching memories")
        return f"Error: {e}"


@mcp.tool(description="List all stored memories for this user.")
async def get_memories() -> str:
    user_id = user_id_var.get(None)
    if not user_id:
        return "Error: user_id not set"
    try:
        result = get_memory_instance().get_all(filters={"user_id": user_id})
        return json.dumps(result)
    except Exception as e:
        logger.exception("Error listing memories")
        return f"Error: {e}"


@mcp.tool(description="Retrieve a specific memory by its ID.")
async def get_memory(memory_id: str) -> str:
    try:
        return json.dumps(get_memory_instance().get(memory_id))
    except Exception as e:
        logger.exception("Error getting memory")
        return f"Error: {e}"


@mcp.tool(description="Update the content or metadata of a specific memory.")
async def update_memory(memory_id: str, text: Optional[str] = None, metadata: Optional[dict] = None) -> str:
    try:
        params: dict = {"memory_id": memory_id}
        if text is not None:
            params["data"] = text
        if metadata is not None:
            params["metadata"] = metadata
        return json.dumps(get_memory_instance().update(**params))
    except Exception as e:
        logger.exception("Error updating memory")
        return f"Error: {e}"


@mcp.tool(description="Delete a specific memory by its ID.")
async def delete_memory(memory_id: str) -> str:
    try:
        get_memory_instance().delete(memory_id=memory_id)
        return "Memory deleted."
    except Exception as e:
        logger.exception("Error deleting memory")
        return f"Error: {e}"


@mcp.tool(description="Delete all memories for this user.")
async def delete_all_memories() -> str:
    user_id = user_id_var.get(None)
    if not user_id:
        return "Error: user_id not set"
    try:
        get_memory_instance().delete_all(user_id=user_id)
        return "All memories deleted."
    except Exception as e:
        logger.exception("Error deleting all memories")
        return f"Error: {e}"


@mcp.tool(description="Delete user/agent/run entities and all their associated memories.")
async def delete_entities(
    user_id: Optional[str] = None, agent_id: Optional[str] = None, run_id: Optional[str] = None
) -> str:
    try:
        m = get_memory_instance()
        deleted = []
        if user_id:
            m.delete_all(user_id=user_id)
            deleted.append(f"user:{user_id}")
        if agent_id:
            m.delete_all(agent_id=agent_id)
            deleted.append(f"agent:{agent_id}")
        if run_id:
            m.delete_all(run_id=run_id)
            deleted.append(f"run:{run_id}")
        if not deleted:
            return "Error: at least one of user_id, agent_id, run_id is required"
        return f"Deleted entities: {', '.join(deleted)}"
    except Exception as e:
        logger.exception("Error deleting entities")
        return f"Error: {e}"


@mcp.tool(description="List all user/agent/run entities.")
async def list_entities() -> str:
    try:
        buckets: dict[tuple[str, str], dict] = {}
        for payload in _iter_payloads():
            created = _parse_timestamp(payload.get("created_at"))
            updated = _parse_timestamp(payload.get("updated_at")) or created
            for entity_type, field in TYPE_TO_FIELD.items():
                value = payload.get(field)
                if not value:
                    continue
                key = (entity_type, str(value))
                bucket = buckets.setdefault(key, {"total_memories": 0, "created_at": None, "updated_at": None})
                bucket["total_memories"] += 1
                if created and (bucket["created_at"] is None or created < bucket["created_at"]):
                    bucket["created_at"] = created
                if updated and (bucket["updated_at"] is None or updated > bucket["updated_at"]):
                    bucket["updated_at"] = updated
        entities = [
            {
                "id": entity_id,
                "type": entity_type,
                "total_memories": data["total_memories"],
                "created_at": data["created_at"].isoformat() if data["created_at"] else None,
                "updated_at": data["updated_at"].isoformat() if data["updated_at"] else None,
            }
            for (entity_type, entity_id), data in sorted(buckets.items())
        ]
        return json.dumps(entities)
    except Exception as e:
        logger.exception("Error listing entities")
        return f"Error: {e}"


@mcp_router.api_route("/", methods=["POST", "GET", "DELETE"])
async def handle_streamable_http(request: Request, _auth=Depends(verify_auth)):
    try:
        user_id, agent_id = _resolve_identity(request)
    except ValueError as e:
        return Response(status_code=400, content=str(e).encode())

    user_token = user_id_var.set(user_id)
    agent_token = agent_id_var.set(agent_id)

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

    try:
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
    finally:
        user_id_var.reset(user_token)
        agent_id_var.reset(agent_token)

    if not response_started:
        return Response(status_code=500, content=b"Transport did not produce a response")

    return Response(
        content=bytes(response_body),
        status_code=response_status,
        headers={k.decode("latin-1"): v.decode("latin-1") for k, v in response_headers},
    )


def setup_mcp_server(app):
    mcp._mcp_server.name = "mem0-mcp-server"
    app.include_router(mcp_router)
