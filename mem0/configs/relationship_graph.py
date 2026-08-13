"""Configuration for the opt-in OSS relationship graph preview."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator


class RelationshipGraphConfig(BaseModel):
    """First-class, default-off configuration for relationship graph memory."""

    enabled: bool = False
    provider: Literal["neo4j"] = "neo4j"
    uri: Optional[str] = None
    username: Optional[str] = None
    password: Optional[SecretStr] = None
    database: str = "neo4j"
    outbox_path: Optional[Path] = None
    bootstrap_schema: bool = True
    auto_start_worker: bool = True
    reset_on_memory_reset: bool = False
    graph_weight: float = Field(default=0.15, ge=0.0, le=1.0)
    candidate_limit: int = Field(default=50, ge=1, le=500)
    explanation_limit: int = Field(default=3, ge=1, le=20)
    worker_poll_seconds: float = Field(default=0.25, gt=0.0, le=60.0)
    worker_lease_seconds: float = Field(default=30.0, gt=0.0, le=3600.0)
    worker_max_attempts: int = Field(default=5, ge=1, le=100)
    max_projection_lag_seconds: float = Field(default=60.0, gt=0.0)
    max_dead_letter_events: int = Field(default=0, ge=0)
    connection_timeout_seconds: float = Field(default=10.0, gt=0.0, le=300.0)
    query_timeout_seconds: float = Field(default=0.25, gt=0.0, le=30.0)
    circuit_breaker_failures: int = Field(default=3, ge=1, le=100)
    circuit_breaker_cooldown_seconds: float = Field(default=30.0, gt=0.0, le=3600.0)

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    @model_validator(mode="before")
    @classmethod
    def load_connection_environment(cls, value):
        if not isinstance(value, dict) or not value.get("enabled", False):
            return value
        configured = dict(value)
        environment = {
            "uri": "MEM0_GRAPH_NEO4J_URI",
            "username": "MEM0_GRAPH_NEO4J_USERNAME",
            "password": "MEM0_GRAPH_NEO4J_PASSWORD",
            "database": "MEM0_GRAPH_NEO4J_DATABASE",
        }
        for field, variable in environment.items():
            if not configured.get(field) and os.environ.get(variable):
                configured[field] = os.environ[variable]
        return configured

    @model_validator(mode="after")
    def require_connection_when_enabled(self):
        if self.enabled and not all((self.uri, self.username, self.password)):
            raise ValueError(
                "enabled relationship_graph requires uri, username, and password "
                "(or MEM0_GRAPH_NEO4J_* environment variables)"
            )
        return self
