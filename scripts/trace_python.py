"""Run an existing Python script/module with opt-in SDK tracing, without rewriting it.

Usage: python scripts/trace_python.py SCRIPT [ARGS...]
       python scripts/trace_python.py --module MODULE [ARGS...]
"""

import os
import runpy
import sys
from pathlib import Path

import mem0.runtime_tracing as runtime_tracing


def main(args=None):
    args = list(sys.argv[1:] if args is None else args)
    if not args or args[0] in {"-h", "--help"}:
        print(__doc__)
        return 0 if args else 2
    module = args[0] == "--module"
    if module and len(args) < 2:
        raise ValueError("--module requires a module name")
    target, forwarded = (args[1], args[2:]) if module else (args[0], args[1:])
    runtime = runtime_tracing.configure_tracing()
    previous_argv = sys.argv
    previous_path = list(sys.path)
    previous_pythonpath = os.environ.get("PYTHONPATH")
    previous_bootstrap = os.environ.get("MEM0_TRACING_CHILD_BOOTSTRAP")
    try:
        if runtime is not None:
            bootstrap = str(Path(runtime_tracing.__file__).parent / "tracing_bootstrap")
            os.environ["PYTHONPATH"] = bootstrap + (os.pathsep + previous_pythonpath if previous_pythonpath else "")
            os.environ["MEM0_TRACING_CHILD_BOOTSTRAP"] = "true"
        sys.argv = [target, *forwarded]
        if module:
            runpy.run_module(target, run_name="__main__", alter_sys=True)
        else:
            path = Path(target).resolve(strict=True)
            sys.path.insert(0, str(path.parent))
            runpy.run_path(str(path), run_name="__main__")
    finally:
        sys.argv = previous_argv
        sys.path[:] = previous_path
        for key, previous in (
            ("PYTHONPATH", previous_pythonpath),
            ("MEM0_TRACING_CHILD_BOOTSTRAP", previous_bootstrap),
        ):
            if previous is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = previous
        runtime_tracing.flush_tracing(runtime)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
