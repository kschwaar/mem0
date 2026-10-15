#!/usr/bin/env python3
"""Sequential local-fork memory smoke evaluation. Does not simulate elapsed years.

Token/phrase checks are a limited proxy, not semantic or factual correctness.
Provider spans cover public adapter methods, not SDK retries, tokens or transport.
Only fresh embedded Qdrant storage is supported. No existing stores are reset.
"""

import argparse
import copy
import hashlib
import json
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


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def model_metadata(config):
    """Keep bounded model/provider identifiers; omit endpoints, credentials and arbitrary config."""
    result = {}
    for role in ("llm", "embedder", "reranker"):
        spec = config.get(role, {})
        if not isinstance(spec, dict):
            continue
        options = spec.get("config", {})
        values = {
            "provider": spec.get("provider"),
            "model": options.get("model") if isinstance(options, dict) else None,
        }
        result[role] = {
            key: value
            if isinstance(value, str)
            and re.fullmatch(r"[\w./:-]{1,128}", value)
            and "://" not in value
            and not value.startswith(("sk-", "Bearer"))
            else None
            for key, value in values.items()
        }
    return result


def grade(expect, output):
    rows = output.get("results") if isinstance(output, dict) else output
    if not isinstance(rows, list) or any(not isinstance(row, dict) or "memory" not in row for row in rows):
        raise ValueError("Unsupported memory output shape")
    text = "\n".join(str(row["memory"]) for row in rows).casefold()
    checks = {}
    for name in ("contains", "excludes"):
        for phrase in expect.get(name, []):
            found = bool(re.search(r"(?<!\w)" + re.escape(phrase.casefold()) + r"(?!\w)", text))
            checks[f"{name}:{phrase}"] = found if name == "contains" else not found
    if "empty" in expect:
        checks["empty"] = (len(rows) == 0) == expect["empty"]
    return checks


def isolated_config(config, directory):
    result = copy.deepcopy(config)
    store = result.get("vector_store", {"provider": "qdrant", "config": {}})
    if store.get("provider") != "qdrant":
        raise ValueError("Only embedded Qdrant is supported")
    if result.get("graph_store") or result.get("relationship_graph"):
        raise ValueError("Graph configuration is unsupported; use a separately isolated graph experiment")
    options = store.get("config", {})
    if any(options.get(key) is not None for key in ("host", "port", "url", "api_key", "client", "https")):
        raise ValueError("Remote or supplied Qdrant clients are forbidden")
    store["config"] = {
        "path": str(directory / "qdrant"),
        "collection_name": "trajectory",
        "embedding_model_dims": options.get("embedding_model_dims", 1536),
        "on_disk": True,
    }
    result["vector_store"] = store
    result["history_db_path"] = str(directory / "history.sqlite")
    return result


def validate_events(events):
    if not isinstance(events, list) or not 1 <= len(events) <= 100:
        raise ValueError("Require 1..100 events")
    ids = set()
    for event in events:
        if not isinstance(event, dict) or not isinstance(event.get("id"), str) or event["id"] in ids:
            raise ValueError("Unique event IDs required")
        ids.add(event["id"])
        action = event.get("action")
        if action not in {"add", "search", "get_all", "reload"}:
            raise ValueError("Unsupported action")
        if action != "reload" and (not isinstance(event.get("user"), str) or not event["user"].strip()):
            raise ValueError("Every operation requires a nonempty user")
        field = "text" if action == "add" else "query" if action == "search" else None
        if field and (not isinstance(event.get(field), str) or not event[field].strip()):
            raise ValueError("Operation input must be a nonempty string")
        expect = event.get("expect", {})
        if not isinstance(expect, dict) or set(expect) - {"contains", "excludes", "empty"}:
            raise ValueError("Unsupported expectations")
        if expect and action not in {"search", "get_all"}:
            raise ValueError("Expectations apply only to memory reads")
        if "empty" in expect and not isinstance(expect["empty"], bool):
            raise ValueError("empty must be boolean")
        for key in ("contains", "excludes"):
            if key in expect and (
                not isinstance(expect[key], list) or any(not isinstance(p, str) or not p.strip() for p in expect[key])
            ):
                raise ValueError("Phrase expectations must be nonempty strings")


def make_memory(config):
    os.environ["MEM0_TELEMETRY"] = "false"
    sys.path.insert(0, str(ROOT))
    import mem0

    if not Path(mem0.__file__).resolve().is_relative_to(ROOT):
        raise RuntimeError("Mem0 must import from this checkout")
    return mem0.Memory.from_config(config)


def close_memory(memory):
    # Memory.close closes SQLite/graph; embedded Qdrant must release its file lock too.
    try:
        memory.close()
    finally:
        client = getattr(getattr(memory, "vector_store", None), "client", None)
        if client is not None:
            client.close()


class Trace:
    def __init__(self, directory, root_id):
        self.directory = directory
        self.root_id = root_id
        self.stage = None
        self.counts = {}
        self.lock = threading.Lock()

    def attach(self, memory):
        for attribute, methods in (
            ("llm", ("generate_response",)),
            ("embedding_model", ("embed", "embed_batch")),
            ("reranker", ("rerank",)),
        ):
            provider = getattr(memory, attribute, None)
            for method in methods:
                original = getattr(provider, method, None)
                if callable(original):
                    setattr(provider, method, self.wrap(original, attribute, method))

    def wrap(self, original, provider, method):
        def call(*args, **kwargs):
            started = time.perf_counter()
            row = {
                "root_trace_id": self.root_id,
                "span_id": uuid.uuid4().hex,
                "stage_id": self.stage,
                "provider_role": provider,
                "method": method,
                "usage": None,
                "physical_attempts": None,
                "status": "success",
            }
            try:
                return original(*args, **kwargs)
            except Exception as exc:
                row.update(status="error", error_type=type(exc).__name__)
                raise
            finally:
                row["elapsed_ms"] = (time.perf_counter() - started) * 1000
                with self.lock:
                    key = f"{row['stage_id']}:{provider}.{method}:{row['status']}"
                    self.counts[key] = self.counts.get(key, 0) + 1
                    with (self.directory / "traces.jsonl").open("a") as stream:
                        stream.write(json.dumps(row) + "\n")

        return call


def run(events, config, output_dir, memory_factory=make_memory, retain_outputs=False):
    directory = Path(output_dir).resolve()
    effective = isolated_config(config, directory)
    validate_events(events)
    directory.mkdir(parents=True, exist_ok=False)
    run_id = uuid.uuid4().hex
    trace = Trace(directory, run_id)
    (directory / "traces.jsonl").touch()
    try:
        revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).strip())
    except (OSError, subprocess.CalledProcessError):
        revision, dirty = None, None
    manifest = {
        "root_trace_id": run_id,
        "git_sha": revision,
        "git_dirty": dirty,
        "python": platform.python_version(),
        "source_script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "dataset_sha256": digest(events),
        "config_sha256": digest(config),
        "models": model_metadata(config),
        "evidence": "local_fork_memory" if memory_factory is make_memory else "test_double",
        "scope": "sequential memory smoke; phrase proxies; adapter-boundary spans; no physical retries/usage",
        "trace_limit": "adapter methods after Memory construction; initialization calls are not captured",
        "operation_latency_scope": "includes initialization/reload when performed during stage",
        "storage": "fresh embedded Qdrant/history beneath output directory",
        "retain_outputs": retain_outputs,
    }
    (directory / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    memory = None
    rows = []
    cleanup_error = None
    try:
        for event in events:
            trace.stage = event["id"]
            row = {
                "root_trace_id": run_id,
                "stage_id": event["id"],
                "action": event["action"],
                "status": "success",
                "checks": {},
            }
            started = time.perf_counter()
            try:
                if memory is None:
                    memory = memory_factory(copy.deepcopy(effective))
                    trace.attach(memory)
                action = event["action"]
                if action == "reload":
                    previous, memory = memory, None
                    close_memory(previous)
                    memory = memory_factory(copy.deepcopy(effective))
                    trace.attach(memory)
                    output = None
                elif action == "add":
                    output = memory.add(event["text"], user_id=f"{run_id}_{event['user']}")
                else:
                    arguments = {"filters": {"user_id": f"{run_id}_{event['user']}"}, "top_k": 20}
                    output = (
                        memory.search(event["query"], **arguments)
                        if action == "search"
                        else memory.get_all(**arguments)
                    )
                if event.get("expect"):
                    try:
                        row["checks"] = grade(event["expect"], output)
                    except ValueError:
                        row["status"] = "ungradable_output"
                if retain_outputs:
                    # Opt-in raw synthetic memory outputs; not a credential-redaction guarantee.
                    row["output"] = output
            except Exception as exc:
                row.update(status="operation_error", error_type=type(exc).__name__)
            row["elapsed_ms"] = (time.perf_counter() - started) * 1000
            rows.append(row)
            with (directory / "outcomes.jsonl").open("a") as stream:
                stream.write(json.dumps(row, default=str) + "\n")
            if row["status"] != "success":
                break  # Later checkpoints would otherwise grade corrupted/uninitialized state.
    finally:
        if memory is not None:
            try:
                close_memory(memory)
            except Exception as exc:
                cleanup_error = type(exc).__name__
    checks = [check for row in rows for check in row["checks"].values()]
    summary = {
        "root_trace_id": run_id,
        "events_completed": len(rows),
        "events_planned": len(events),
        "checks_total": len(checks),
        "checks_passed": sum(checks),
        "operation_errors": sum(row["status"] == "operation_error" for row in rows),
        "ungradable_outputs": sum(row["status"] == "ungradable_output" for row in rows),
        "stage_adapter_counts": trace.counts,
        "cleanup_error_type": cleanup_error,
        "quality_passed": all(checks) if checks else None,
        "assessment": "phrase_proxy_checks" if checks else "operation_smoke_only",
        "passed": len(rows) == len(events)
        and all(checks)
        and all(row["status"] == "success" for row in rows)
        and cleanup_error is None,
    }
    (directory / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True, help="Mem0 JSON config; calls its configured providers")
    parser.add_argument("--dataset", type=Path, default=ROOT / "scripts/evaluation/trajectory.json")
    parser.add_argument("--output", type=Path, required=True, help="New directory only; never reuse existing storage")
    parser.add_argument("--retain-outputs", action="store_true", help="Opt-in raw outputs; use synthetic inputs only")
    args = parser.parse_args()
    try:
        summary = run(
            json.loads(args.dataset.read_text()),
            json.loads(args.config.read_text()),
            args.output,
            retain_outputs=args.retain_outputs,
        )
    except Exception as exc:
        print(f"Trajectory setup/cleanup failed ({type(exc).__name__}); inspect artifacts if created", file=sys.stderr)
        return 2
    print(json.dumps(summary, indent=2))
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
