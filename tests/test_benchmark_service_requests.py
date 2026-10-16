"""Product HTTP payload contracts without importing or starting the service."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
from benchmark_service_requests import plan  # noqa: E402


def test_add_preserves_prompt_date_scope_and_removes_gold():
    result = plan("add", {
        "messages": [{"role": "user", "content": "tea", "has_answer": True}],
        "user_id": "isolated", "custom_instructions": "retain preferences", "observation_date": "2023-05-21",
    })
    assert result["method"] == "POST" and result["path"] == "/memories"
    assert result["json"]["user_id"] == "isolated"
    assert result["json"]["prompt"] == "retain preferences"
    assert result["json"]["messages"] == [{"role": "user", "content": "[Source session date: 2023-05-21]\ntea"}]


def test_search_and_listing_use_filters_and_top_k():
    result = plan("search", {"query": "tea", "user_id": "isolated", "limit": 10})
    assert result == {"method": "POST", "path": "/search", "json": {
        "query": "tea", "filters": {"user_id": "isolated"}, "top_k": 10,
    }}
    listing = plan("list", {"user_id": "isolated", "limit": 10})
    assert listing["params"]["top_k"] == 10
    assert listing["params"] == {"user_id": "isolated", "top_k": 10}


def test_listing_rejects_filter_not_exposed_by_product_api():
    with pytest.raises(ValueError):
        plan("list", {"user_id": "isolated", "filters": {"topic": "tea"}})


@pytest.mark.parametrize("rerank", [True, False])
def test_unsupported_rerank_is_never_silently_ignored(rerank):
    with pytest.raises(ValueError):
        plan("search", {"query": "tea", "user_id": "isolated", "rerank": rerank})


def test_unscoped_and_destructive_operations_are_rejected():
    with pytest.raises(ValueError):
        plan("list", {})
    with pytest.raises(ValueError):
        plan("delete", {"user_id": "isolated"})
