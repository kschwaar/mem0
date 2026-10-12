"""Model-free tests of source-content overlap grouping."""

import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "benchmark_overlap", Path(__file__).parents[1] / "scripts/audit_benchmark_overlap.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def record(question, session, text, label=False):
    return {
        "question_id": question,
        "haystack_session_ids": [session],
        "haystack_sessions": [[{"role": "user", "content": text, "has_answer": label}]],
    }


def test_shared_ids_and_equal_content_form_transitive_components():
    result = module.audit([
        record("a", "shared", "one"), record("b", "shared", "two"),
        record("c", "different", "two", True), record("d", "independent", "three"),
    ])
    assert result["components"] == [["a", "b", "c"], ["d"]]


def test_role_is_part_of_content_identity():
    left = record("a", "s1", "one")
    right = record("b", "s2", "one")
    right["haystack_sessions"][0][0]["role"] = "assistant"
    assert module.audit([left, right])["component_count"] == 2


def test_invalid_alignment_and_duplicate_questions_rejected():
    item = record("a", "s", "one")
    with pytest.raises(ValueError):
        module.audit([item, item])
    item["haystack_session_ids"] = []
    with pytest.raises(ValueError):
        module.audit([item])


def test_history_plan_verifies_source_and_preserves_complete_work(tmp_path):
    import hashlib
    import json

    item = record("q", "s", "tea")
    item["haystack_dates"] = ["2023-01-01"]
    path = tmp_path / "source.json"
    raw = json.dumps([item]).encode()
    path.write_bytes(raw)
    digest = hashlib.sha256(raw).hexdigest()
    plan = module.plan_history(path, digest, "q")
    assert plan["chunks"] == 1 and plan["content_characters"] == 3
    assert plan["dataset_sha256"] == digest
    with pytest.raises(ValueError, match="budget"):
        module.plan_history(path, digest, "q", max_characters=2)
    with pytest.raises(ValueError, match="exactly one"):
        module.plan_history(path, digest, "absent")
    path.write_bytes(raw + b" ")
    with pytest.raises(ValueError, match="provenance"):
        module.plan_history(path, digest, "q")
