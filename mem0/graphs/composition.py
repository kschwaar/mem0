"""Programmatic composition for explicit relationship-graph backfill."""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Protocol

from mem0.graphs.backfill import (
    BackfillCheckpoint,
    BackfillRunRequest,
    GraphMemoryInspector,
    JsonBackfillCheckpointStore,
    RelationshipGraphBackfillRunner,
)
from mem0.graphs.extractors import RelationshipExtractor
from mem0.graphs.models import GraphScope
from mem0.graphs.readers import VectorStoreCanonicalMemoryReader, VectorStoreListProvider
from mem0.graphs.service import MemoryGraphProjectionService, RelationshipGraphProjectionAdapter


class CanonicalMemorySource(Protocol):
    """Memory attributes needed to compose an explicit backfill."""

    collection_name: str
    vector_store: VectorStoreListProvider


class RelationshipGraphBackfillAdapter(RelationshipGraphProjectionAdapter, GraphMemoryInspector, Protocol):
    """Combined graph operations required by the backfill composition."""


class RelationshipGraphBackfill:
    """Wire an OSS Memory source to explicit, resumable graph backfill."""

    def __init__(
        self,
        *,
        memory: CanonicalMemorySource,
        graph: RelationshipGraphBackfillAdapter,
        extractor: RelationshipExtractor,
        checkpoint_directory: Path,
        max_records: int = 10_000,
    ):
        collection_name = memory.collection_name.strip()
        reader = VectorStoreCanonicalMemoryReader(
            vector_store=memory.vector_store,
            collection_name=collection_name,
            max_records=max_records,
        )
        checkpoints = JsonBackfillCheckpointStore(checkpoint_directory)
        projector = MemoryGraphProjectionService(extractor=extractor, adapter=graph)

        self.collection_name = collection_name
        self.checkpoint_directory = Path(checkpoint_directory)
        self._runner = RelationshipGraphBackfillRunner(
            reader=reader,
            inspector=graph,
            projector=projector,
            checkpoints=checkpoints,
        )

    def run(
        self,
        *,
        run_id: str,
        scope: GraphScope,
        page_size: int = 50,
        max_memories: Optional[int] = None,
    ) -> BackfillCheckpoint:
        """Run or resume one exact-scope backfill."""
        return self._runner.run(
            BackfillRunRequest(
                run_id=run_id,
                collection_name=self.collection_name,
                scope=scope,
                page_size=page_size,
                max_memories=max_memories,
            )
        )
