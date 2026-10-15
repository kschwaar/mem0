"""Versioned correctness adapter for the pinned LongMemEval answer scorer.

Install with ``install_longmemeval_v1(run_module)`` immediately after importing
``benchmarks.longmemeval.run``. It wraps the suite's own answer scoring entrypoints;
retrieval scoring and frozen benchmark files remain untouched.
"""

from __future__ import annotations

import inspect
import json
from typing import Any

from mem0.memory.utils import remove_code_blocks

PROTOCOL_VERSION = "longmemeval-answer-judge-v1"
SUITE_ADAPTER_VERSION = "longmemeval-checkpoint-v1"

_JUDGE_SYSTEM = """You are a neutral benchmark answer judge. Determine whether the candidate response answers the question consistently with the reference answer. The reference may be a rubric. Do not favor yes or no. Do not infer facts absent from the candidate response. A response is an abstention only when it declines or says it cannot determine the requested answer; an uncertain or incomplete answer is not a correct answer. For a non-abstention item, an abstention is incorrect. For an explicitly abstention item, a clear abstention is correct. For factual answers, require all required facts, reject contradictions, and allow valid semantic paraphrases and equivalent units. Return only one JSON object with fields: verdict ('correct' or 'incorrect'), response_type ('factual', 'abstention', or 'uncertain'), response_evidence (an exact, contiguous quotation from the candidate response that supports the classification; empty only when no support exists), and reason (brief)."""


def build_judge_prompt(
    *, question_id: str, question: str, answer: str, response: str,
    question_type: str, question_date: str = "",
) -> str:
    """Build neutral structured input with benchmark identity-derived abstention truth."""
    payload = {
        "question_id": question_id,
        "question_type": question_type,
        "question": question,
        "reference_answer": str(answer),
        "candidate_response": response,
        "question_date": question_date,
        "is_abstention": question_id.endswith("_abs"),
    }
    return _JUDGE_SYSTEM + "\n\nEvaluation item (JSON):\n" + json.dumps(payload, ensure_ascii=False)


def normalize_judgment(raw: Any, candidate_answer: str, is_abstention: bool) -> dict[str, Any]:
    """Validate structured judgment and fail closed without conflating protocol errors."""
    error = None
    parsed = raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(remove_code_blocks(raw))
        except (json.JSONDecodeError, TypeError):
            parsed = None
            error = "malformed_or_truncated_json"
    if not isinstance(parsed, dict):
        error = error or "judgment_not_object"
        parsed = {}

    verdict = parsed.get("verdict")
    response_type = parsed.get("response_type")
    evidence = parsed.get("response_evidence")
    if not isinstance(verdict, str) or verdict not in {"correct", "incorrect"}:
        error = error or "invalid_verdict"
    if not isinstance(response_type, str) or response_type not in {"factual", "abstention", "uncertain"}:
        error = error or "invalid_response_type"
    if not isinstance(evidence, str):
        error = error or "invalid_response_evidence"
        evidence = ""
    if evidence and evidence not in candidate_answer:
        error = error or "response_evidence_not_literal_candidate_text"
    if verdict == "correct" and not evidence:
        error = error or "correct_verdict_without_response_evidence"

    valid = error is None
    semantic_correct = valid and verdict == "correct"
    if valid and response_type == "abstention" and not is_abstention:
        semantic_correct = False
    if valid and is_abstention and response_type != "abstention":
        semantic_correct = False
    if valid and response_type == "uncertain":
        semantic_correct = False
    return {
        "verdict": "correct" if semantic_correct else "incorrect",
        "score": 1.0 if semantic_correct else 0.0,
        "response_type": response_type if valid else None,
        "response_evidence": evidence if valid else None,
        "reason": parsed.get("reason") if isinstance(parsed.get("reason"), str) else None,
        "judge_error": error,
        "protocol_version": PROTOCOL_VERSION,
    }


def _generation_error(exc: Exception) -> str:
    """Retain the pinned client's incomplete-output context without exposing text."""
    seen = set()
    current = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, RuntimeError) and str(current) == "Evaluation output incomplete or empty":
            return "incomplete_or_empty_generation"
        if isinstance(current, TimeoutError) or type(current).__name__ == "APITimeoutError":
            return "generation_timeout"
        if type(current).__name__ in {"APIConnectionError", "ConnectError", "ConnectionError"}:
            return "transport_error"
        current = current.__cause__ or current.__context__
    return "generation_error"


class _JudgeProxy:
    """Give the pinned runner its yes/no interface over structured judgments."""

    def __init__(self, client: Any, question_id: str):
        self._client = client
        self._question_id = question_id
        self.records: list[dict[str, Any]] = []

    async def judge_yes_no(self, prompt: str) -> tuple[bool, str]:
        # Candidate evidence comes from the exact prompt built by the suite adapter.
        try:
            payload = json.loads(prompt.rsplit("Evaluation item (JSON):\n", 1)[1])
        except (IndexError, json.JSONDecodeError, TypeError):
            payload = {}
        is_abstention = self._question_id.endswith("_abs")
        if payload.get("question_id") != self._question_id:
            self.records.append(self._error_record("invalid_suite_judge_prompt"))
            return False, json.dumps({"judge_error": "invalid_suite_judge_prompt"})
        if payload.get("is_abstention") is not is_abstention:
            self.records.append(self._error_record("abstention_identity_mismatch"))
            return False, json.dumps({"judge_error": "abstention_identity_mismatch"})
        candidate = payload.get("candidate_response")
        if not isinstance(candidate, str):
            self.records.append(self._error_record("candidate_response_missing"))
            return False, json.dumps({"judge_error": "candidate_response_missing"})
        try:
            raw = await self._client.generate(system="", user=prompt, temperature=0)
        except Exception as exc:  # generation failures are not semantic incorrect judgments
            error = _generation_error(exc)
            record = self._error_record(error)
            record["judge_error_detail"] = type(exc).__name__
            self.records.append(record)
            return False, json.dumps({"judge_error": error, "error_type": type(exc).__name__})
        record = normalize_judgment(raw, candidate, is_abstention)
        self.records.append(record)
        raw_text = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False)
        return record["score"] == 1.0, raw_text

    @staticmethod
    def _error_record(error: str) -> dict[str, Any]:
        return {
            "verdict": "error", "score": 0.0, "response_type": None,
            "response_evidence": None, "reason": None, "judge_error": error,
            "protocol_version": PROTOCOL_VERSION,
        }


def _instrument_result(result: dict[str, Any], proxy: _JudgeProxy) -> dict[str, Any]:
    for cutoff, record in zip(result.get("cutoff_results", {}).values(), proxy.records):
        cutoff["judge_protocol_version"] = PROTOCOL_VERSION
        cutoff["judge_error"] = record.get("judge_error")
        cutoff["response_type"] = record.get("response_type")
        cutoff["response_evidence"] = record.get("response_evidence")
        cutoff["judge_reason"] = record.get("reason")
        if record.get("judge_error"):
            cutoff["judgment"] = "ERROR"
        cutoff["score"] = record["score"]
    return result


def install_longmemeval_v1(run_module: Any) -> None:
    """Adapt pinned suite answer scoring in memory, once per imported run module."""
    if getattr(run_module, "_mem0_judge_adapter", None) == SUITE_ADAPTER_VERSION:
        return
    process = run_module.process_question_answerer
    saved = run_module.apply_longmemeval_answerer_judge_to_saved_result
    if not {"question", "judge_llm"}.issubset(inspect.signature(process).parameters):
        raise RuntimeError("Unsupported LongMemEval process_question_answerer entrypoint")
    if not {"result", "judge_llm"}.issubset(inspect.signature(saved).parameters):
        raise RuntimeError("Unsupported LongMemEval saved-result scoring entrypoint")

    run_module.get_judge_prompt = build_judge_prompt

    async def adapted_process_question_answerer(*args: Any, **kwargs: Any) -> dict[str, Any]:
        bound = inspect.signature(process).bind_partial(*args, **kwargs)
        question = bound.arguments["question"]
        proxy = _JudgeProxy(bound.arguments["judge_llm"], question["question_id"])
        bound.arguments["judge_llm"] = proxy
        result = await process(*bound.args, **bound.kwargs)
        return _instrument_result(result, proxy)

    async def adapted_saved_result(*args: Any, **kwargs: Any) -> None:
        bound = inspect.signature(saved).bind_partial(*args, **kwargs)
        result = bound.arguments["result"]
        proxy = _JudgeProxy(bound.arguments["judge_llm"], result["question_id"])
        bound.arguments["judge_llm"] = proxy
        await saved(*bound.args, **bound.kwargs)
        _instrument_result(result, proxy)

    run_module.process_question_answerer = adapted_process_question_answerer
    run_module.apply_longmemeval_answerer_judge_to_saved_result = adapted_saved_result
    run_module._mem0_judge_adapter = SUITE_ADAPTER_VERSION


def main() -> None:
    """Run the existing suite CLI with the repaired answer judge installed."""
    from benchmarks.longmemeval import run

    install_longmemeval_v1(run)
    run.main()


if __name__ == "__main__":
    main()
