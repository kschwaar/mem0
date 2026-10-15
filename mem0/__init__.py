import importlib.metadata

__version__ = importlib.metadata.version("mem0ai")

from mem0.client.main import AsyncMemoryClient, MemoryClient  # noqa
from mem0.memory.main import AsyncMemory, Memory  # noqa
from mem0.runtime_tracing import configure_tracing, flush_tracing, trace_call  # noqa
