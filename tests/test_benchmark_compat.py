"""Contract regressions for the pinned benchmark wrapper, without inference."""

import importlib.util
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "benchmark_compat", Path(__file__).parents[1] / "scripts/benchmark_compat.py"
)
compat = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(compat)


def test_search_translates_scope_and_limit_without_mutating_request():
    request = {"query": "drink", "user_id": "alice", "limit": 10, "filters": {"topic": "food"}, "rerank": True}
    result = compat.search_kwargs(request)
    assert result == {"query": "drink", "filters": {"topic": "food", "user_id": "alice"}, "top_k": 10, "rerank": True}
    assert request["filters"] == {"topic": "food"}


@pytest.mark.parametrize(
    "payload",
    [
        {"query": "x", "user_id": "alice", "filters": {"user_id": "bob"}},
        {"query": "x", "limit": 1},
        {"query": "x", "user_id": "alice", "limit": True},
        {"query": "x", "user_id": "alice", "reference_date": "2020-01-01"},
    ],
)
def test_invalid_or_unsupported_search_is_rejected(payload):
    with pytest.raises(ValueError):
        compat.search_kwargs(payload)


def test_dates_are_preserved_as_context_never_unsupported_sdk_timestamps():
    request = {
        "messages": [{"role": "user", "content": "I like tea", "has_answer": True}],
        "user_id": "alice",
        "timestamp": 0,
        "custom_instructions": "Store preferences",
        "metadata": {"source": "pilot"},
    }
    result = compat.add_kwargs(request)
    assert "timestamp" not in result
    assert result["prompt"] == "Store preferences"
    assert result["messages"] == [
        {"role": "user", "content": "[Source session date: 1970-01-01T00:00:00+00:00]\nI like tea"}
    ]
    assert result["metadata"]["evaluation_source_date"] == "1970-01-01T00:00:00+00:00"
    assert request["messages"][0]["content"] == "I like tea"


def test_missing_date_is_not_fabricated():
    result = compat.add_kwargs({"messages": [{"role": "user", "content": "Hi"}], "user_id": "alice"})
    assert result == {"messages": [{"role": "user", "content": "Hi"}], "user_id": "alice"}


@pytest.mark.parametrize("date", [True, float("nan"), "not a date"])
def test_bad_dates_fail_instead_of_becoming_current_time(date):
    with pytest.raises(ValueError):
        compat.add_kwargs({"messages": [{"role": "user", "content": "Hi"}], "user_id": "alice", "timestamp": date})


def test_scoped_listing_and_valid_empty_results_are_supported():
    assert compat.list_kwargs({"user_id": "alice", "limit": 4}) == {"filters": {"user_id": "alice"}, "top_k": 4}
    assert compat.validate_result({"results": []}) == {"results": []}


@pytest.mark.parametrize(
    "result", [None, {}, {"results": "bad"}, {"results": [{}]}, {"results": [{"id": "m1", "memory": 4}]}]
)
def test_malformed_results_never_become_legitimate_empty_results(result):
    with pytest.raises(ValueError):
        compat.validate_result(result)


def test_result_preserves_state_and_mutation_information():
    response = {"results": [{"id": "m1", "memory": "tea", "event": "UPDATE"}], "relations": []}
    assert compat.validate_result(response) is response
