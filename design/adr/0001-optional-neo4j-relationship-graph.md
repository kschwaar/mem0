# ADR-0001: Project an optional Neo4j relationship graph from OSS memories

- Status: Proposed — requires design review before implementation
- Date: 2026-07-20
- Decision owners: Mem0 OSS maintainers

## Context

Mem0 v3 stores canonical memory text and embeddings in the configured vector
store, history in SQLite, and schema-free entity-to-memory links in a second
vector collection. This provides ranking signals but does not retain typed,
traversable entity-to-entity facts, edge provenance, or temporal state.

Earlier graph-store support coupled graph mutation to the hot path, used
provider-specific implementations, and could not reliably tie a relationship
to the memory that asserted it. Restoring that design would reintroduce its
operational and consistency risks.

## Decision

Add Neo4j first as an *optional, asynchronous relationship projection*. It is
an additional read model and never replaces the existing vector store, SQLite
history, or built-in entity store.

The source of truth remains the canonical memory record. Neo4j is updated via
an idempotent persistent outbox after a successful memory lifecycle operation.
The graph is allowed to be briefly stale; an unavailable graph must not make
normal memory writes or searches fail.

Typed facts are represented as reified `RelationshipAssertion` nodes, rather
than dynamic Neo4j relationship types. This keeps Cypher parameterized, allows
one asserted fact to have multiple source memories, and gives each assertion
its own temporal and provenance state.

The first read integration supplies graph-derived candidate memory IDs and an
explanation to the existing scorer. It does not expose arbitrary Cypher or
replace semantic, BM25, reranker, or current entity-boost results.

## Consequences

### Positive

- Restores typed, directed, multi-hop relationship retrieval.
- Makes edge provenance, validity periods, and graph visualization possible.
- Keeps the zero-configuration v3 path unchanged and preserves graceful
  degradation when Neo4j is unavailable.
- Avoids a provider matrix until the Neo4j contract and operational model are
  proven.

### Negative

- The graph is eventually consistent with canonical memory storage.
- Adds a Neo4j deployment, credentials, monitoring, backups, and a graph
  migration/backfill operation for adopters.
- Requires a durable outbox and reconciliation process, plus a new security
  boundary for tenant scope enforcement.

## Rejected alternatives

1. **Restore the prior graph-store classes unchanged.** They target the
   pre-v3 extraction/write model and would restore duplicated provider logic
   and text-re-extraction deletes.
2. **Make Neo4j the canonical memory store.** This would disrupt every vector
   store integration and remove the current local/offline path.
3. **Store predicates as dynamic Cypher relationship types.** Dynamic labels
   and relationship types are not parameterized safely; a fixed schema with a
   `predicate` property is safer and supports richer provenance.
4. **Synchronously dual-write on `Memory.add()`.** A Neo4j outage would turn an
   optional feature into a write outage and still would not create a distributed
   transaction with the vector store.
