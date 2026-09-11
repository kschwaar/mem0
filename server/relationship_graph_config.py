"""Environment-backed configuration for the optional relationship graph."""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Optional


def build_relationship_graph_config(
    provider: str,
    environment: Mapping[str, str] = os.environ,
) -> Optional[dict[str, object]]:
    """Build the SDK relationship-graph config for the selected server provider."""
    if provider.lower() != "neo4j":
        return None

    return {
        "enabled": True,
        "provider": "neo4j",
        "uri": environment.get("MEM0_GRAPH_NEO4J_URI")
        or environment.get("NEO4J_URL", "bolt://neo4j-mem0:7687"),
        "username": environment.get("MEM0_GRAPH_NEO4J_USERNAME")
        or environment.get("NEO4J_USERNAME", "neo4j"),
        "password": environment.get("MEM0_GRAPH_NEO4J_PASSWORD")
        or environment.get("NEO4J_PASSWORD", ""),
        "database": environment.get("MEM0_GRAPH_NEO4J_DATABASE", "neo4j"),
        "query_timeout_seconds": float(environment.get("MEM0_GRAPH_QUERY_TIMEOUT_SECONDS", "5")),
    }
