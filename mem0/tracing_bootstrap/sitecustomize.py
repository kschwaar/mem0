"""Opt-in subprocess bootstrap installed on PYTHONPATH by trace_python.py only."""

import importlib.machinery
import importlib.util
import os
import sys
from pathlib import Path

# Keep existing startup hooks (including resource guards) rather than shadowing
# them with the tracing hook. Resolve outside our injected bootstrap directory.
bootstrap = Path(__file__).resolve().parent
original = importlib.machinery.PathFinder.find_spec(
    "sitecustomize",
    [entry for entry in sys.path if Path(entry).resolve() != bootstrap],
)
if original is not None and original.loader is not None:
    module = importlib.util.module_from_spec(original)
    sys.modules["sitecustomize"] = module
    original.loader.exec_module(module)
if os.environ.get("MEM0_TRACING_CHILD_BOOTSTRAP") == "true" and os.environ.get("MEM0_RUNTIME_TRACING") == "true":
    try:
        from mem0.runtime_tracing import configure_tracing

        configure_tracing()
    except Exception:
        # Match optional runtime instrumentation: never log credentials/tracebacks
        # or replace the target program's inference behavior on telemetry failure.
        sys.stderr.write("Mem0 child tracing setup failed; check tracing dependencies and local configuration.\n")
