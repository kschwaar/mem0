"""Remote forgetting must not claim completeness after bounded enumeration."""

from urllib.parse import parse_qs, urlparse

import memory_core


def test_forget_does_not_delete_a_partial_list_when_pagination_limit_is_reached(monkeypatch, tmp_path):
    repo = memory_core.resolve_repo(str(tmp_path))
    stored = {
        f"mem-{index}": {"id": f"mem-{index}", "app_id": repo.app_id}
        for index in range(4)
    }
    original_ids = set(stored)

    def request_json(url, key, payload, timeout):
        query = parse_qs(urlparse(url).query)
        page = int(query["page"][0])
        size = int(query["page_size"][0])
        records = list(stored.values())
        return {"results": records[(page - 1) * size : page * size]}, 200, {}

    def delete_memory(url, key, memory_id):
        del stored[memory_id]
        return True

    monkeypatch.setattr(memory_core, "FORGET_PAGE_SIZE", 2)
    monkeypatch.setattr(memory_core, "FORGET_MAX_PAGES", 2)
    monkeypatch.setattr(memory_core, "api_key", lambda: "test-key")
    monkeypatch.setattr(memory_core, "api_base_url", lambda: "http://localhost.invalid")
    monkeypatch.setattr(memory_core, "user_id", lambda: "pagination-test")
    monkeypatch.setattr(memory_core, "_request_json", request_json)
    monkeypatch.setattr(memory_core, "_delete_memory", delete_memory)

    result = memory_core.forget_remote_repo(repo)

    assert result["status"] == "error"
    assert set(stored) == original_ids
