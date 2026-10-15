"""Contract tests for structured LongMemEval answer judgments; no inference."""

import importlib.util
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "benchmark_judge", Path(__file__).parents[1] / "scripts" / "benchmark_judge.py"
)
judge = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(judge)


def judgment(verdict, response_type, evidence):
    return {
        "verdict": verdict,
        "response_type": response_type,
        "response_evidence": evidence,
        "reason": "supported by candidate text",
    }


def test_unquoted_gold_cannot_certify_non_abstention_refusal():
    result = judge.normalize_judgment(
        judgment("correct", "factual", "33"),
        candidate_answer="I don't have the information.",
        is_abstention=False,
    )
    assert result["score"] == 0.0
    assert result["judge_error"] == "response_evidence_not_literal_candidate_text"


def test_grounded_non_abstention_refusal_is_incorrect():
    result = judge.normalize_judgment(
        judgment("correct", "abstention", "I don't have the information."),
        candidate_answer="I don't have the information.",
        is_abstention=False,
    )
    assert result["score"] == 0.0
    assert result["judge_error"] is None
    assert result["response_type"] == "abstention"


def test_grounded_abstention_matches_explicit_abstention_ground_truth():
    result = judge.normalize_judgment(
        judgment("correct", "abstention", "I cannot determine that."),
        candidate_answer="I cannot determine that.",
        is_abstention=True,
    )
    assert result["score"] == 1.0
    assert result["verdict"] == "correct"


def test_literal_candidate_support_can_certify_semantic_paraphrase():
    result = judge.normalize_judgment(
        judgment("correct", "factual", "She is a doctor."),
        candidate_answer="She is a doctor.",
        is_abstention=False,
    )
    assert result["score"] == 1.0
    assert result["response_evidence"] == "She is a doctor."


def test_malformed_truncated_and_untyped_judgments_are_protocol_errors():
    for raw in (
        '{"verdict":"correct","response_evidence":',
        {"verdict": "correct"},
        judgment("correct", "unsure", "The meeting is Tuesday."),
    ):
        result = judge.normalize_judgment(raw, "The meeting is Tuesday.", False)
        assert result["score"] == 0.0
        assert result["verdict"] == "incorrect"
        assert result["judge_error"] is not None


def test_factual_answer_cannot_pass_an_explicit_abstention_item():
    result = judge.normalize_judgment(
        judgment("correct", "factual", "33"),
        candidate_answer="33",
        is_abstention=True,
    )
    assert result["score"] == 0.0
    assert result["judge_error"] is None


def test_uncertainty_cannot_be_certified_as_a_correct_answer():
    result = judge.normalize_judgment(
        judgment("correct", "uncertain", "Maybe it was Tuesday."),
        candidate_answer="Maybe it was Tuesday.",
        is_abstention=False,
    )
    assert result["score"] == 0.0
    assert result["judge_error"] is None


def test_complete_fenced_json_judgment_is_not_a_protocol_failure():
    raw = '```json\n{"verdict":"correct","response_type":"factual","response_evidence":"33"}\n```'
    result = judge.normalize_judgment(raw, candidate_answer="33", is_abstention=False)
    assert result["score"] == 1.0
    assert result["judge_error"] is None


@pytest.mark.asyncio
async def test_incomplete_generation_is_not_reported_as_a_transport_failure():
    class IncompleteClient:
        async def generate(self, **_kwargs):
            try:
                raise RuntimeError("Evaluation output incomplete or empty")
            except RuntimeError:
                raise RuntimeError("Evaluation generation failed; not retried") from None

    proxy = judge._JudgeProxy(IncompleteClient(), "question")
    prompt = judge.build_judge_prompt(
        question_id="question", question="Age?", answer="33", response="33", question_type="multi-session"
    )
    correct, _raw = await proxy.judge_yes_no(prompt)
    assert correct is False
    assert proxy.records[0]["judge_error"] == "incomplete_or_empty_generation"
