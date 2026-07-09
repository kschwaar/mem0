"""Shared Mem0 API URL helpers for plugin scripts."""

from __future__ import annotations

import os

DEFAULT_API_URL = "https://api.mem0.ai"
DEFAULT_MCP_URL = "https://mcp.mem0.ai/mcp"


def api_base_url() -> str:
    return os.environ.get("MEM0_BASE_URL", DEFAULT_API_URL).rstrip("/")


def api_url(path: str) -> str:
    return f"{api_base_url()}/{path.lstrip('/')}"


def mcp_url() -> str:
    return os.environ.get("MEM0_MCP_URL", DEFAULT_MCP_URL).rstrip("/")
