KNOWN_MEMORY_ID = "3f971ed1-6cd5-4ead-9b05-18ef13b3430c"
KNOWN_MEMORY = {
    "id": KNOWN_MEMORY_ID,
    "memory": "prefers dark mode",
    "user_id": "user-1",
    "app_id": "app-1",
}


def test_native_get_returns_the_requested_known_memory(native_http):
    response = native_http(
        [{"method": "GET", "path": f"/memories/{KNOWN_MEMORY_ID}"}],
        memories=[KNOWN_MEMORY],
    )[0]

    assert response["status"] == 200, response
    assert response["body"] == KNOWN_MEMORY, response


def test_native_get_returns_404_for_valid_missing_uuid(native_http):
    response = native_http(
        [{"method": "GET", "path": f"/memories/{KNOWN_MEMORY_ID}"}],
    )[0]

    assert response["status"] == 404, response


def test_native_get_maps_storage_failure_to_structured_502(native_http):
    response = native_http(
        [{"method": "GET", "path": f"/memories/{KNOWN_MEMORY_ID}"}],
        fail_get=True,
    )[0]

    assert response["status"] == 502, response
    assert response["body"]["code"] == "datastore_unavailable", response
    assert response["body"]["request_id"] == response["request_id_header"], response


def test_stateless_mcp_rejects_get_stream_and_still_accepts_post(native_http):
    headers = {"Accept": "application/json, text/event-stream"}
    responses = native_http([
        {"method": "GET", "path": "/mcp", "headers": headers},
        {"method": "GET", "path": "/mcp/", "headers": headers},
        {
            "method": "POST", "path": "/mcp", "headers": headers,
            "json": {"jsonrpc": "2.0", "id": 7, "method": "ping"},
        },
    ])

    for response in responses[:2]:
        assert response["status"] == 405, response
        assert "POST" in response["allow_header"], response
    assert responses[2]["status"] == 200, responses[2]
    assert responses[2]["body"] == {"jsonrpc": "2.0", "id": 7, "result": {}}
