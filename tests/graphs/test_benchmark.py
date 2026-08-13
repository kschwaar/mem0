import json
from pathlib import Path

from mem0.graphs.benchmark import RetrievalBenchmarkCase, run_retrieval_benchmark


FIXTURE = Path(__file__).parents[1] / "fixtures" / "relationship_graph_retrieval.json"


def cases():
    return [RetrievalBenchmarkCase.model_validate(case) for case in json.loads(FIXTURE.read_text())]


def test_bundled_preview_gate_improves_mrr_without_expanding_candidates():
    result = run_retrieval_benchmark(cases(), k=2, iterations=5)

    assert result.passed is True
    assert result.recall_delta >= 0
    assert result.mean_reciprocal_rank_delta > 0
    assert result.graph_only_expansion_enabled is False


def test_gate_fails_when_required_quality_delta_is_not_met():
    result = run_retrieval_benchmark(cases(), k=2, iterations=1, min_recall_delta=0.5)

    assert result.passed is False
