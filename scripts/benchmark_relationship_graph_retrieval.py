#!/usr/bin/env python3
"""Run the relationship-graph preview retrieval gate against a JSON dataset."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from mem0.graphs.benchmark import RetrievalBenchmarkCase, run_retrieval_benchmark


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--k", type=int, default=3)
    parser.add_argument("--iterations", type=int, default=100)
    parser.add_argument("--graph-weight", type=float, default=0.15)
    parser.add_argument("--min-recall-delta", type=float, default=0.0)
    parser.add_argument("--min-mrr-delta", type=float, default=0.0)
    parser.add_argument("--max-p95-latency-overhead-ms", type=float, default=5.0)
    arguments = parser.parse_args()
    raw_cases = json.loads(arguments.dataset.read_text(encoding="utf-8"))
    cases = [RetrievalBenchmarkCase.model_validate(case) for case in raw_cases]
    result = run_retrieval_benchmark(
        cases,
        k=arguments.k,
        iterations=arguments.iterations,
        graph_weight=arguments.graph_weight,
        min_recall_delta=arguments.min_recall_delta,
        min_mrr_delta=arguments.min_mrr_delta,
        max_p95_latency_overhead_ms=arguments.max_p95_latency_overhead_ms,
    )
    print(result.model_dump_json(indent=2))
    return 0 if result.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
