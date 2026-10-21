MEMORIES = [
    {
        "id": "memory-1",
        "memory": "favorite color is blue",
        "user_id": "user-1",
        "app_id": "app-1",
    },
    {
        "id": "memory-2",
        "memory": "favorite color is blue",
        "user_id": "user-1",
        "app_id": "app-2",
    },
    {
        "id": "memory-3",
        "memory": "favorite color is blue",
        "user_id": "user-2",
        "app_id": "app-1",
    },
]


def test_list_memories_are_scoped_to_user_and_app(native_http):
    response = native_http(
        [{"method": "GET", "path": "/memories?user_id=user-1&app_id=app-1"}],
        memories=MEMORIES,
    )[0]

    assert response["status"] == 200, response
    assert [memory["id"] for memory in response["body"]["results"]] == ["memory-1"]


def test_search_memories_are_scoped_to_user_and_app(native_http):
    response = native_http(
        [
            {
                "method": "POST",
                "path": "/search",
                "json": {"query": "favorite color", "user_id": "user-1", "app_id": "app-1"},
            }
        ],
        memories=MEMORIES,
    )[0]

    assert response["status"] == 200, response
    assert [memory["id"] for memory in response["body"]["results"]] == ["memory-1"]


def test_delete_memories_preserves_other_apps_and_users(native_http):
    responses = native_http(
        [
            {"method": "DELETE", "path": "/memories?user_id=user-1&app_id=app-1"},
            {"method": "GET", "path": "/memories?user_id=user-1"},
            {"method": "GET", "path": "/memories?app_id=app-1"},
        ],
        memories=MEMORIES,
    )

    assert responses[0]["status"] == 200, responses[0]
    assert [memory["id"] for memory in responses[1]["body"]["results"]] == ["memory-2"]
    assert [memory["id"] for memory in responses[2]["body"]["results"]] == ["memory-3"]
