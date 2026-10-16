#!/usr/bin/env python3
"""OMP lifecycle entry point for the shared Mem0 hook runtime."""

from __future__ import annotations

import sys
from pathlib import Path

_here = Path(__file__).resolve()
_core_dir = _here.parents[2] / "core"
sys.path.insert(0, str(_core_dir))

import hook_runner  # noqa: E402
import telemetry  # noqa: E402
from memory_core import configure_harness, record_tool  # noqa: E402

configure_harness("omp", data_dir_name="claude-code-plugin", source_tag="omp_plugin")
telemetry.init(harness="omp", source_tag="OMP_PLUGIN")


if __name__ == "__main__":
    hook_runner.entry_point(
        extra_actions={
            "post-tool-failure": lambda store, event: record_tool(store, event, failed=True),
        },
        data_dir_env="MEM0_CODE_DATA_DIR",
        automatic_flush_reasons={"session-end", "pre-compact"},
    )
