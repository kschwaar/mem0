"""Exact cross-source overlap checks do not invoke models."""

import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "cross_text", Path(__file__).parents[1] / "scripts/audit_cross_dataset_text.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_whitespace_matches_without_label_or_role_leakage():
    left = [{"question_id": "a", "haystack_sessions": [[
        {"content": "hello\nworld", "has_answer": True}, {"content": " "},
    ]]}]
    right = [{"sample_id": "b", "conversation": {
        "speaker_a": "Name", "session_1_date_time": "date",
        "session_1": [{"dia_id": "D1", "text": "hello world"}, {"dia_id": "D2", "text": "Hello world"}],
    }}]
    result = module.compare(left, right)
    assert result["locomo_turns_checked"] == 2 and result["matched_turns"] == 1
    assert result["matches"][0]["longmemeval_question_ids"] == ["a"]
    assert result["blank_turns_excluded"] == {"longmemeval": 1, "locomo": 0}
    assert result["acceptance_independence"] == "unproven"
