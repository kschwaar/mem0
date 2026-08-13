"""Deterministic offline quality and latency gate for graph candidate reranking."""

from __future__ import annotations

import math
import time
from collections.abc import Iterable
from typing import Callable

from pydantic import BaseModel, ConfigDict, Field, model_validator

from mem0.utils.scoring import score_and_rank


class RetrievalBenchmarkCandidate(BaseModel):
    id: str = Field(min_length=1)
    semantic_score: float = Field(ge=0.0, le=1.0)
    graph_score: float = Field(default=0.0, ge=0.0, le=1.0)

    model_config = ConfigDict(extra="forbid", frozen=True)


class RetrievalBenchmarkCase(BaseModel):
    query: str = Field(min_length=1)
    candidates: tuple[RetrievalBenchmarkCandidate, ...] = Field(min_length=1)
    relevant_ids: frozenset[str] = Field(min_length=1)

    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def relevant_ids_must_be_semantic_candidates(self):
        candidate_ids = {candidate.id for candidate in self.candidates}
        if not self.relevant_ids.issubset(candidate_ids):
            raise ValueError("relevant_ids must be present in the semantic candidate set")
        return self


class RetrievalBenchmarkMetrics(BaseModel):
    recall_at_k: float = Field(ge=0.0, le=1.0)
    mean_reciprocal_rank: float = Field(ge=0.0, le=1.0)
    p95_latency_ms: float = Field(ge=0.0)

    model_config = ConfigDict(extra="forbid", frozen=True)


class RetrievalBenchmarkResult(BaseModel):
    cases: int = Field(ge=1)
    k: int = Field(ge=1)
    baseline: RetrievalBenchmarkMetrics
    graph: RetrievalBenchmarkMetrics
    recall_delta: float
    mean_reciprocal_rank_delta: float
    p95_latency_overhead_ms: float
    passed: bool
    graph_only_expansion_enabled: bool = False

    model_config = ConfigDict(extra="forbid", frozen=True)


def run_retrieval_benchmark(
    cases: Iterable[RetrievalBenchmarkCase],
    *,
    k: int = 3,
    graph_weight: float = 0.15,
    iterations: int = 100,
    min_recall_delta: float = 0.0,
    min_mrr_delta: float = 0.0,
    max_p95_latency_overhead_ms: float = 5.0,
    clock: Callable[[], int] = time.perf_counter_ns,
) -> RetrievalBenchmarkResult:
    """Compare baseline and graph reranking over the same semantic candidate sets."""
    benchmark_cases = list(cases)
    if not benchmark_cases:
        raise ValueError("benchmark requires at least one case")
    if k < 1 or iterations < 1:
        raise ValueError("k and iterations must be positive")

    baseline_rankings = [_rank(case, k=k, graph_weight=0.0) for case in benchmark_cases]
    graph_rankings = [_rank(case, k=k, graph_weight=graph_weight) for case in benchmark_cases]
    baseline_latency = _measure(benchmark_cases, k, 0.0, iterations, clock)
    graph_latency = _measure(benchmark_cases, k, graph_weight, iterations, clock)
    baseline = RetrievalBenchmarkMetrics(
        recall_at_k=_recall(baseline_rankings, benchmark_cases),
        mean_reciprocal_rank=_mrr(baseline_rankings, benchmark_cases),
        p95_latency_ms=_percentile(baseline_latency, 0.95),
    )
    graph = RetrievalBenchmarkMetrics(
        recall_at_k=_recall(graph_rankings, benchmark_cases),
        mean_reciprocal_rank=_mrr(graph_rankings, benchmark_cases),
        p95_latency_ms=_percentile(graph_latency, 0.95),
    )
    recall_delta = graph.recall_at_k - baseline.recall_at_k
    mrr_delta = graph.mean_reciprocal_rank - baseline.mean_reciprocal_rank
    overhead = max(0.0, graph.p95_latency_ms - baseline.p95_latency_ms)
    return RetrievalBenchmarkResult(
        cases=len(benchmark_cases),
        k=k,
        baseline=baseline,
        graph=graph,
        recall_delta=recall_delta,
        mean_reciprocal_rank_delta=mrr_delta,
        p95_latency_overhead_ms=overhead,
        passed=(
            recall_delta >= min_recall_delta and mrr_delta >= min_mrr_delta and overhead <= max_p95_latency_overhead_ms
        ),
    )


def _rank(case: RetrievalBenchmarkCase, *, k: int, graph_weight: float) -> list[str]:
    candidates = [{"id": candidate.id, "score": candidate.semantic_score} for candidate in case.candidates]
    graph_scores = {candidate.id: candidate.graph_score for candidate in case.candidates if candidate.graph_score > 0}
    return [
        result["id"]
        for result in score_and_rank(
            semantic_results=candidates,
            bm25_scores={},
            entity_boosts={},
            threshold=0.0,
            top_k=k,
            graph_scores=graph_scores,
            graph_weight=graph_weight,
        )
    ]


def _measure(cases, k, graph_weight, iterations, clock):
    samples = []
    for _ in range(iterations):
        for case in cases:
            started = clock()
            _rank(case, k=k, graph_weight=graph_weight)
            samples.append((clock() - started) / 1_000_000)
    return samples


def _recall(rankings, cases):
    return sum(
        len(set(ranking) & case.relevant_ids) / len(case.relevant_ids) for ranking, case in zip(rankings, cases)
    ) / len(cases)


def _mrr(rankings, cases):
    values = []
    for ranking, case in zip(rankings, cases):
        rank = next((index for index, memory_id in enumerate(ranking, start=1) if memory_id in case.relevant_ids), None)
        values.append(0.0 if rank is None else 1.0 / rank)
    return sum(values) / len(values)


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * percentile) - 1)]
