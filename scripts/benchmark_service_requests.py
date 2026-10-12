"""Pure request plans for this fork's product HTTP API, not stock OSS routes.

No transport, credentials, retries, cleanup or writes are performed here.
"""

from benchmark_compat import add_kwargs, list_kwargs, search_kwargs


def plan(operation, request):
    if operation == "add":
        body = add_kwargs(request)
        allowed = {"messages", "user_id", "agent_id", "run_id", "metadata", "prompt"}
        if set(body) - allowed:
            raise ValueError("Product add does not support arbitrary scope fields")
        return {"method": "POST", "path": "/memories", "json": body}
    if operation == "search":
        # Product SearchRequest has no rerank field. Even False cannot be silently
        # ignored: SDK defaults may differ from the requested behavior.
        if "rerank" in request:
            raise ValueError("Product search cannot express explicit rerank behavior")
        return {"method": "POST", "path": "/search", "json": search_kwargs(request)}
    if operation == "list":
        translated = list_kwargs(request)
        scope = translated["filters"]
        if set(scope) - {"user_id", "agent_id", "run_id", "app_id"}:
            raise ValueError("Product listing supports entity IDs only, not arbitrary filters")
        params = {**scope, "top_k": translated["top_k"]}
        return {"method": "GET", "path": "/memories", "params": params}
    raise ValueError("Only bounded add/search/list plans are supported")
