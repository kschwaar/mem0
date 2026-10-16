#!/usr/bin/env python3
"""Bounded provider-adapter evaluations; stdlib-only in offline simulation mode.

Case inputs/outputs may contain private data. Use synthetic cases: local artifacts
retain outputs for review. Config values and exception messages are never recorded.
Latency budgets are post-completion checks, not request cancellation or timeouts.
"""

import argparse
import ast
import concurrent.futures
import hashlib
import importlib.util
import json
import math
import os
import platform
import re
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TASK_CONFIG = {"llm": "llm", "embedding": "embedder", "rerank": "reranker"}
EXPECT_KEYS = {
    "json_required",
    "json_type",
    "required_keys",
    "equals",
    "contains",
    "tool_names",
    "dimensions",
    "top_id",
    "finite_vectors",
    "additive_schema",
    "relationship_schema",
    "memory_items",
    "memory_count",
}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def quantile(values, probability):
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    low = math.floor(position)
    high = math.ceil(position)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def wilson(passed, total):
    if not total:
        return [None, None]
    z = 1.959963984540054
    rate = passed / total
    denominator = 1 + z * z / total
    middle = (rate + z * z / (2 * total)) / denominator
    margin = z * math.sqrt(rate * (1 - rate) / total + z * z / (4 * total * total)) / denominator
    return [max(0, middle - margin), min(1, middle + margin)]


def grade(case, output):
    expected = case["expect"]
    checks = {}
    parsed = output
    if (
        expected.get("json_required")
        or "json_type" in expected
        or "required_keys" in expected
        or any(k in expected for k in ("memory_items", "memory_count", "additive_schema", "relationship_schema"))
    ):
        try:
            parsed = json.loads(output) if isinstance(output, str) else output
            checks["json_valid"] = True
        except (ValueError, TypeError):
            parsed = None
            checks["json_valid"] = False
    types = {"object": dict, "array": list, "string": str, "number": (int, float), "boolean": bool, "null": type(None)}
    if "json_type" in expected:
        checks["json_type"] = isinstance(parsed, types[expected["json_type"]])
        if expected["json_type"] == "number" and isinstance(parsed, bool):
            checks["json_type"] = False
    if "required_keys" in expected:
        checks["required_keys"] = isinstance(parsed, dict) and all(k in parsed for k in expected["required_keys"])
    if "equals" in expected:
        checks["equals"] = parsed == expected["equals"]
    if "contains" in expected:
        text_output = output if isinstance(output, str) else json.dumps(output, ensure_ascii=False)
        checks["contains"] = all(part in text_output for part in expected["contains"])
    if "tool_names" in expected:
        calls = output.get("tool_calls", output.get("toolCalls", [])) if isinstance(output, dict) else []
        names = [c.get("name") for c in calls if isinstance(c, dict)]
        checks["tool_names"] = sorted(names) == sorted(expected["tool_names"])
    if "dimensions" in expected or expected.get("finite_vectors"):
        vectors = output if "texts" in case["input"] else [output]
        checks["dimensions"] = (
            isinstance(vectors, list)
            and len(vectors) == (len(case["input"]["texts"]) if "texts" in case["input"] else 1)
            and all(
                isinstance(v, list) and ("dimensions" not in expected or len(v) == expected["dimensions"])
                for v in vectors
            )
        )
        checks["finite_vectors"] = checks["dimensions"] and all(
            isinstance(n, (int, float)) and not isinstance(n, bool) and math.isfinite(n) for v in vectors for n in v
        )
    if "top_id" in expected:
        checks["top_id"] = bool(output) and isinstance(output, list) and isinstance(output[0], dict)
        checks["top_id"] = checks["top_id"] and output[0].get("id") == expected["top_id"]
    memories = parsed.get("memory") if isinstance(parsed, dict) else None
    if "memory_count" in expected:
        checks["memory_count"] = isinstance(memories, list) and len(memories) == expected["memory_count"]
    if "memory_items" in expected:
        checks["memory_items"] = isinstance(memories, list) and all(
            any(
                isinstance(item, dict)
                and all(
                    all(word.casefold() in str(item.get("text", "")).casefold() for word in value)
                    if key == "contains"
                    else item.get(key) == value
                    for key, value in wanted.items()
                )
                for item in memories
            )
            for wanted in expected["memory_items"]
        )
    if expected.get("additive_schema"):
        existing = {m["id"] for m in case["input"].get("prompt_args", {}).get("existing_memories", [])}
        checks["additive_schema"] = isinstance(memories, list) and all(
            isinstance(item, dict)
            and item.get("event") in {"ADD", "UPDATE", "DELETE", "NONE"}
            and isinstance(item.get("id"), str)
            and isinstance(item.get("text"), str)
            and bool(item["text"].strip())
            and item.get("attributed_to") in {"user", "assistant"}
            and (item["event"] == "ADD" or item["id"] in existing)
            and (item["event"] != "UPDATE" or isinstance(item.get("old_memory"), str))
            for item in memories
        )
        if checks["additive_schema"]:
            checks["additive_schema"] = len({m["id"] for m in memories}) == len(memories)
    if expected.get("relationship_schema"):
        relationships = parsed.get("relationships") if isinstance(parsed, dict) else None
        checks["relationship_schema"] = isinstance(relationships, list) and set(parsed) == {"relationships"}
        if checks["relationship_schema"]:
            for relationship in relationships:
                valid = isinstance(relationship, dict)
                valid = valid and all(
                    isinstance(relationship.get(key), dict)
                    and isinstance(relationship[key].get("text"), str)
                    and bool(relationship[key]["text"].strip())
                    and bool(re.fullmatch(r"[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*", relationship[key].get("semantic_type", "")))
                    for key in ("subject", "object")
                )
                valid = valid and isinstance(relationship.get("predicate"), str)
                valid = valid and bool(re.fullmatch(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*", relationship["predicate"]))
                confidence = relationship.get("confidence") if valid else None
                valid = valid and isinstance(confidence, (int, float)) and not isinstance(confidence, bool)
                valid = valid and math.isfinite(confidence) and 0 <= confidence <= 1
                checks["relationship_schema"] = checks["relationship_schema"] and valid
        # Basic structural check only; canonical Pydantic constraints are exercised in product tests.
    return checks


def validate_cases(cases, offline):
    ids = set()
    for case in cases:
        if not isinstance(case, dict):
            raise ValueError("Cases must be objects")
        if not isinstance(case.get("id"), str) or not case["id"] or case["id"] in ids:
            raise ValueError("Case IDs must be unique nonempty strings")
        ids.add(case["id"])
        if case.get("task") not in TASK_CONFIG or not isinstance(case.get("input"), dict):
            raise ValueError("Every case requires a supported task and input object")
        expected = case.get("expect")
        if not isinstance(expected, dict) or not expected or set(expected) - EXPECT_KEYS:
            raise ValueError("Every case requires nonempty supported expectations")
        for key in ("json_required", "finite_vectors", "additive_schema", "relationship_schema"):
            if key in expected and expected[key] is not True:
                raise ValueError("Boolean expectations must be true")
        if expected.get("json_type", "object") not in {"object", "array", "string", "number", "boolean", "null"}:
            raise ValueError("Unsupported json_type")
        for key in ("dimensions", "memory_count"):
            if key in expected and (
                isinstance(expected[key], bool)
                or not isinstance(expected[key], int)
                or expected[key] < (1 if key == "dimensions" else 0)
            ):
                raise ValueError("Invalid expected count or dimensions")
        for key in ("contains", "required_keys", "tool_names"):
            if key in expected and (
                not isinstance(expected[key], list) or not all(isinstance(v, str) and v for v in expected[key])
            ):
                raise ValueError("Expected list of strings")
        for key in ("contains", "required_keys", "memory_items"):
            if key in expected and not expected[key]:
                raise ValueError("Empty expectations are vacuous")
        if "memory_items" in expected:
            if not isinstance(expected["memory_items"], list) or any(
                not isinstance(m, dict)
                or not m
                or (set(m) == {"contains"} and not m["contains"])
                or set(m) - {"event", "id", "attributed_to", "contains"}
                or any(
                    (not isinstance(v, list) or not all(isinstance(x, str) and x for x in v))
                    if k == "contains"
                    else not isinstance(v, str)
                    for k, v in m.items()
                )
                for m in expected["memory_items"]
            ):
                raise ValueError("Invalid memory expectations")
        if case["task"] != "embedding" and ({"finite_vectors", "dimensions"} & set(expected)):
            raise ValueError("Vector expectations require embedding task")
        inputs = render_inputs(case)
        if case["task"] == "llm":
            if not isinstance(inputs.get("messages"), list) or any(
                not isinstance(m, dict)
                or m.get("role") not in {"system", "user", "assistant", "tool"}
                or "content" not in m
                for m in inputs["messages"]
            ):
                raise ValueError("LLM input requires messages")
        elif case["task"] == "embedding":
            if ("text" in inputs) == ("texts" in inputs):
                raise ValueError("Embedding input requires exactly one of text/texts")
            texts = inputs.get("texts", [inputs.get("text")])
            if not isinstance(texts, list) or not texts or not all(isinstance(t, str) for t in texts):
                raise ValueError("Embedding input must contain text strings")
            if inputs.get("memory_action", "search") not in {"add", "search", "update"}:
                raise ValueError("Invalid memory action")
        else:
            if not isinstance(inputs.get("query"), str) or not isinstance(inputs.get("documents"), list):
                raise ValueError("Rerank requires query and documents")
            if any(not isinstance(d, dict) or not isinstance(d.get("memory"), str) for d in inputs["documents"]):
                raise ValueError("Rerank documents require memory text")
            if "top_k" in inputs and (
                isinstance(inputs["top_k"], bool) or not isinstance(inputs["top_k"], int) or inputs["top_k"] < 1
            ):
                raise ValueError("Invalid rerank top_k")
        if offline and "mock_output" not in case and "mock_error" not in case:
            raise ValueError("Offline cases require mock_output or mock_error")
    if not cases:
        raise ValueError("Dataset must not be empty")


def safe_provider_metadata(config):
    result = {}
    for task, key in TASK_CONFIG.items():
        spec = config.get(key, {})
        if not isinstance(spec, dict):
            continue
        settings = spec.get("config", {})
        if not isinstance(settings, dict):
            raise ValueError("Provider config must be an object")
        metadata = {"provider": spec.get("provider"), "requested_model": settings.get("model"), "actual_model": None}
        # Values for these fields are useful experiment identity, never endpoint/credential values.
        for name in ("temperature", "top_p", "max_tokens", "seed", "embedding_dims", "dimensions"):
            value = settings.get(name)
            if value is not None:
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                    raise ValueError("Sampling/dimension settings must be finite numbers")
                metadata[name] = value
        if any(
            v is not None and (not isinstance(v, str) or len(v) > 255)
            for v in (metadata["provider"], metadata["requested_model"])
        ):
            raise ValueError("Provider and model identifiers must be bounded strings")
        if spec:
            result[task] = metadata
    return result


def summarize_rows(rows):
    passed = sum(r["passed"] for r in rows)
    latencies = [r["latency_ms"] for r in rows if r["latency_ms"] is not None]
    return {
        "trials": len(rows),
        "passed": passed,
        "pass_rate": passed / len(rows),
        "pass_rate_wilson_95": wilson(passed, len(rows)),
        "error_count": sum(r["status"] in {"error", "initialization_error"} for r in rows),
        "latency_ms": {
            "p50": quantile(latencies, 0.5),
            "p95": quantile(latencies, 0.95),
            "p99": quantile(latencies, 0.99),
        },
    }


def render_inputs(case):
    inputs = dict(case["input"])
    stage = inputs.pop("stage", None)
    if stage is None:
        return inputs
    if case["task"] != "llm":
        raise ValueError("Prompt stages require llm task")
    if stage == "additive_extraction":
        # Load current stdlib-only prompt source without importing mem0.__init__.
        spec = importlib.util.spec_from_file_location("mem0_eval_prompts", ROOT / "mem0/configs/prompts.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        system = module.ADDITIVE_EXTRACTION_PROMPT
        if inputs.pop("agent_scoped", False):
            system += module.AGENT_CONTEXT_SUFFIX
        user = module.generate_additive_extraction_prompt(**inputs.pop("prompt_args"))
    elif stage == "relationship_extraction":
        tree = ast.parse((ROOT / "mem0/graphs/extractors.py").read_text())
        assignment = next(
            node
            for node in tree.body
            if isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "RELATIONSHIP_EXTRACTION_PROMPT" for t in node.targets)
        )
        system = ast.literal_eval(assignment.value)
        user = inputs.pop("memory_text")
    else:
        raise ValueError("Unsupported prompt stage")
    inputs["messages"] = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    return inputs


def make_adapter(task, config):
    # Lazy import: simulation and artifact analysis require no Mem0 dependencies.
    sys.path.insert(0, str(ROOT))
    from mem0.utils.factory import EmbedderFactory, LlmFactory, RerankerFactory

    spec = config[TASK_CONFIG[task]]
    provider_config = spec.get("config", {})
    if task == "llm":
        return LlmFactory.create(spec["provider"], provider_config)
    if task == "embedding":
        return EmbedderFactory.create(spec["provider"], provider_config, None)
    return RerankerFactory.create(spec["provider"], provider_config)


def invoke(task, adapter, inputs):
    if task == "llm":
        return adapter.generate_response(**inputs)
    if task == "embedding":
        action = inputs.get("memory_action", "search")
        if "texts" in inputs:
            return adapter.embed_batch(inputs["texts"], action)
        return adapter.embed(inputs["text"], action)
    return adapter.rerank(inputs["query"], inputs["documents"], inputs.get("top_k"))


def evaluate(
    cases,
    config,
    output_dir,
    repeat=1,
    concurrency=1,
    offline=False,
    latency_budget_ms=None,
    min_pass_rate=1.0,
    adapter_factory=make_adapter,
):
    validate_cases(cases, offline)
    if not 1 <= repeat <= 100 or not 1 <= concurrency <= 64 or len(cases) * repeat > 10000:
        raise ValueError("Bounds: repeat 1..100, concurrency 1..64, at most 10000 trials")
    if not 0 <= min_pass_rate <= 1 or (latency_budget_ms is not None and latency_budget_ms <= 0):
        raise ValueError("Invalid pass-rate or latency gate")
    if not offline:
        for task in {c["task"] for c in cases}:
            spec = config.get(TASK_CONFIG[task])
            if not isinstance(spec, dict) or not isinstance(spec.get("provider"), str):
                raise ValueError("Missing provider configuration for requested task")
    provider_metadata = safe_provider_metadata(config)
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=False)
    run_id = uuid.uuid4().hex
    evidence = "offline_simulation" if offline else "live_provider_adapter"
    try:
        revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).strip())
    except (OSError, subprocess.CalledProcessError):
        revision, dirty = None, None
    # Hash config, retain key names only: values can contain credentials, private endpoints or custom prompts.
    manifest = {
        "schema_version": 1,
        "run_id": run_id,
        "evidence": evidence,
        "git_sha": revision,
        "git_dirty": dirty,
        "python": platform.python_version(),
        "dataset_sha256": digest(cases),
        "config_sha256": digest(config),
        "providers": provider_metadata,
        "config_keys": {k: sorted(v.get("config", {})) for k, v in config.items() if isinstance(v, dict)},
        "repeat": repeat,
        "concurrency": concurrency,
        "latency_budget_ms": latency_budget_ms,
        "min_pass_rate": min_pass_rate,
        "coverage": "provider adapter boundary only; internal calls/retries/usage not traced",
        "usage_source": "unavailable",
        "latency_scope": "adapter call; excludes adapter initialization",
        "request_timeout": "provider config only; latency gate does not cancel work",
    }
    (directory / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    local = threading.local()

    def trial(case, repetition):
        row = {
            "schema_version": 1,
            "run_id": run_id,
            "trial_id": uuid.uuid4().hex,
            "case_id": case["id"],
            "task": case["task"],
            "provider_metadata": provider_metadata.get(case["task"], {}),
            "repetition": repetition,
            "evidence": evidence,
            "input_sha256": digest(case["input"]),
            "usage": None,
            "usage_source": "unavailable",
            "physical_attempts": None,
            "initialization_ms": 0.0,
        }
        start = None
        try:
            if not offline:
                if not hasattr(local, "adapters"):
                    local.adapters = {}
                if case["task"] not in local.adapters:
                    init_start = time.perf_counter()
                    local.adapters[case["task"]] = adapter_factory(case["task"], config)
                    row["initialization_ms"] = (time.perf_counter() - init_start) * 1000
                adapter = local.adapters[case["task"]]
            inputs = render_inputs(case)
            row["rendered_request_sha256"] = digest(inputs)
            row["started_at_unix"] = time.time()
            start = time.perf_counter()
            if offline:
                if "mock_error" in case:
                    raise RuntimeError("Simulated failure")
                output = case["mock_output"]
            else:
                output = invoke(case["task"], adapter, inputs)
            row["latency_ms"] = (time.perf_counter() - start) * 1000
            row["checks"] = grade(case, output)
            if latency_budget_ms is not None:
                row["checks"]["latency_budget"] = row["latency_ms"] <= latency_budget_ms
            row["passed"] = all(row["checks"].values())
            row["status"] = "passed" if row["passed"] else "failed_expectation"
            row["output"] = output
            # Detect non-JSON adapter outputs now, so artifact writing cannot lose all trial records.
            json.dumps(row, allow_nan=False)
        except Exception as error:
            row.pop("output", None)
            row.update(
                status="initialization_error" if start is None else "error",
                passed=False,
                error_type=type(error).__name__,
                checks={},
                latency_ms=None if start is None else (time.perf_counter() - start) * 1000,
            )
            # Exception messages can contain headers/keys; intentionally omit them.
        return row

    started = time.perf_counter()
    rows = []
    with (directory / "trials.jsonl").open("w") as ledger:
        with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = [pool.submit(trial, case, repetition) for repetition in range(repeat) for case in cases]
            for future in concurrent.futures.as_completed(futures):
                row = future.result()
                rows.append(row)
                ledger.write(json.dumps(row, allow_nan=False) + "\n")
                ledger.flush()
    elapsed = time.perf_counter() - started
    summary = {
        "run_id": run_id,
        "evidence": evidence,
        **summarize_rows(rows),
        "elapsed_seconds": elapsed,
        "completed_trials_per_second": len(rows) / elapsed,
        "gate_passed": sum(r["passed"] for r in rows) / len(rows) >= min_pass_rate,
        "by_task": {
            task: summarize_rows([r for r in rows if r["task"] == task]) for task in sorted({r["task"] for r in rows})
        },
        "by_case": {
            case_id: summarize_rows([r for r in rows if r["case_id"] == case_id])
            for case_id in sorted({r["case_id"] for r in rows})
        },
    }
    (directory / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--latency-budget-ms", type=float)
    parser.add_argument("--min-pass-rate", type=float, default=1.0)
    args = parser.parse_args(argv)
    if not args.offline and args.config is None:
        parser.error("Live evaluations require --config")
    # Disable usage analytics for synthetic eval runs before lazy Mem0 import.
    os.environ["MEM0_TELEMETRY"] = "false"
    try:
        cases = [json.loads(line) for line in args.cases.read_text().splitlines() if line.strip()]
        config = json.loads(args.config.read_text()) if args.config else {}
        summary = evaluate(
            cases,
            config,
            args.output_dir,
            args.repeat,
            args.concurrency,
            args.offline,
            args.latency_budget_ms,
            args.min_pass_rate,
        )
    except (ValueError, OSError, KeyError, TypeError):
        print("Invalid dataset/configuration or output directory; no exception content logged.", file=sys.stderr)
        return 2
    print(json.dumps(summary, indent=2))
    return 0 if summary["gate_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
