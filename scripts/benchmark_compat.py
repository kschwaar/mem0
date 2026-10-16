"""Pure request/response translation for an isolated benchmark Python SDK wrapper.

No services or models are imported here. The HTTP wrapper must call these helpers
explicitly; this module alone does not install or instrument a running service.
Dates use an explicit source-date-in-text protocol, not historical storage time.
"""

import math
from datetime import date, datetime, timezone

SOURCE_TIME_PROTOCOL = "benchmark-source-time-v1"


def _valid_source_date(value):
    if not isinstance(value, str):
        return False
    try:
        if len(value) == 10:
            date.fromisoformat(value)
        elif len(value) > 10 and value[10] in {"T", "t", " "}:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
        else:
            return False
    except ValueError:
        return False
    return True


def _fields(request, allowed):
    if not isinstance(request, dict) or set(request) - allowed:
        raise ValueError("Unsupported benchmark request fields")


def _scope(request):
    filters = request.get("filters", {})
    if not isinstance(filters, dict):
        raise ValueError("filters must be an object")
    filters = dict(filters)
    for key in ("user_id", "agent_id", "run_id"):
        value = request.get(key)
        if value is not None:
            if not isinstance(value, str) or not value.strip():
                raise ValueError("Scope IDs must be nonempty strings")
            if key in filters and filters[key] != value:
                raise ValueError("Conflicting scope IDs")
            filters[key] = value
    if not any(isinstance(filters.get(key), str) and filters[key].strip() for key in ("user_id", "agent_id", "run_id")):
        raise ValueError("At least one explicit scope ID is required")
    return filters


def _limit(request):
    value = request.get("limit", 20)
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 1000:
        raise ValueError("limit must be an integer in 1..1000")
    return value


def search_kwargs(request):
    """Translate the benchmark request to Memory.search, rejecting hidden options."""
    _fields(request, {"query", "user_id", "agent_id", "run_id", "filters", "limit", "rerank"})
    if not isinstance(request.get("query"), str) or not request["query"].strip():
        raise ValueError("A nonempty query is required")
    result = {"query": request["query"], "filters": _scope(request), "top_k": _limit(request)}
    if "rerank" in request:
        if not isinstance(request["rerank"], bool):
            raise ValueError("rerank must be boolean")
        result["rerank"] = request["rerank"]
    return result


def list_kwargs(request):
    """Translate a scoped listing; never permit an unscoped benchmark scan."""
    _fields(request, {"user_id", "agent_id", "run_id", "filters", "limit"})
    return {"filters": _scope(request), "top_k": _limit(request)}


def _source_date(request):
    timestamp = request.get("timestamp")
    observation = request.get("observation_date")
    if timestamp is not None and observation is not None:
        raise ValueError("Specify only one source date")
    try:
        if timestamp is not None:
            if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)) or not math.isfinite(timestamp):
                raise ValueError("timestamp must be a finite Unix epoch")
            return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()
        if observation is not None:
            if not isinstance(observation, str):
                raise ValueError("observation_date must be YYYY-MM-DD")
            datetime.strptime(observation, "%Y-%m-%d")
            return observation
    except (OverflowError, OSError, ValueError) as exc:
        raise ValueError("Invalid source date") from exc
    return None


def add_kwargs(request):
    """Preserve historical context in text, omit unsupported timestamp arguments.

    `has_answer` is a labeling sidecar and is intentionally stripped from model
    messages. Existing metadata is copied, and the protocol field is reserved.
    """
    _fields(
        request,
        {
            "messages",
            "user_id",
            "agent_id",
            "run_id",
            "timestamp",
            "observation_date",
            "custom_instructions",
            "metadata",
        },
    )
    scope = _scope(request)
    messages = request.get("messages")
    if not isinstance(messages, list) or not messages:
        raise ValueError("Nonempty messages are required")
    copied = []
    for message in messages:
        if (
            not isinstance(message, dict)
            or set(message) - {"role", "content", "has_answer"}
            or message.get("role") not in {"user", "assistant", "system"}
            or not isinstance(message.get("content"), str)
            or not message["content"].strip()
        ):
            raise ValueError("Invalid benchmark message")
        copied.append({"role": message["role"], "content": message["content"]})
    result = {"messages": copied, **scope}
    metadata = request.get("metadata")
    if metadata is not None:
        if not isinstance(metadata, dict) or "evaluation_source_date" in metadata:
            raise ValueError("Invalid or conflicting benchmark metadata")
        result["metadata"] = dict(metadata)
    source_date = _source_date(request)
    if source_date is not None:
        copied[0]["content"] = f"[Source session date: {source_date}]\n{copied[0]['content']}"
        result.setdefault("metadata", {})["evaluation_source_date"] = source_date
    if request.get("custom_instructions") is not None:
        if not isinstance(request["custom_instructions"], str) or not request["custom_instructions"].strip():
            raise ValueError("custom_instructions must be nonempty text")
        result["prompt"] = request["custom_instructions"]
    return result


def validate_result(response):
    """Validate an SDK envelope without converting errors into empty results."""
    if not isinstance(response, dict) or not isinstance(response.get("results"), list):
        raise ValueError("Invalid benchmark result envelope")
    for row in response["results"]:
        if (
            not isinstance(row, dict)
            or not isinstance(row.get("id"), str)
            or not row["id"].strip()
            or not isinstance(row.get("memory"), str)
        ):
            raise ValueError("Invalid benchmark memory result")
    return response


def normalize_source_dates(response):
    """Project trusted source dates onto suite chronology without changing HTTP evidence."""
    validate_result(response)
    projected = dict(response)
    rows = []
    for row in response["results"]:
        metadata = row.get("metadata")
        if isinstance(metadata, dict) and "evaluation_source_date" in metadata:
            source_date = metadata["evaluation_source_date"]
            if not _valid_source_date(source_date):
                raise ValueError("Invalid evaluation_source_date")
            projected_row = dict(row)
            if "created_at" in row:
                if "storage_created_at" not in row:
                    projected_row["storage_created_at"] = row["created_at"]
            projected_row["created_at"] = source_date
            rows.append(projected_row)
        else:
            rows.append(row)
    projected["results"] = rows
    return projected
