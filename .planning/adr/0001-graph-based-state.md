# ADR 0001 — Graph-based state over relational schema

**Status**: Accepted
**Date**: pre-2026 (decision is foundational)

## Context

jvagent must hold the state of:

- An app (singleton).
- Multiple agents per app.
- Pluggable actions per agent, with their own attributes and child caches.
- Per-user memory with conversation history, including bidirectional chaining and rolling-window pruning.

A relational schema would require: tables for agents, actions, conversations, interactions, plus FK relationships and migration discipline for every plugin that adds state. Cascading delete becomes a SQL constraint exercise; plugin extension requires schema migrations.

## Decision

State is modeled as a **graph of typed nodes connected by edges**, persisted via jvspatial. Each entity (`App`, `Agent`, `Action`, `User`, `Conversation`, `Interaction`) is a `Node` subclass; relationships are `Edge`s; traversal is a `Walker`.

The graph is the schema. No migration is needed when a plugin attaches a new child Node — it just `connect()`s.

## Consequences

### Positive
- **Plugin extension is free.** Authors connect new nodes to existing ones; no schema migration.
- **Walker traversal == control flow.** The walker visits nodes in graph order. Routing logic lives on the action rather than in a central dispatcher.
- **Per-user state is local.** A user's entire history is a connected subgraph; cascading delete is one edge-walk; isolation is by construction.
- **jvspatial owns the persistence layer**, so jvagent does not pick a backend for the user. JSON, SQLite, MongoDB, DynamoDB are all first-class.
- **Bidirectional chaining is natural** — `Interaction ↔ Interaction` edges replace a brittle `prev_id`/`next_id` column pair.

### Negative
- **Ad-hoc analytics are harder.** No SQL `JOIN` for "every Interaction across all users". Mitigated by keeping logs in a separate `logs` DB (still graph-shaped but flat) and by per-Interaction aggregation dicts.
- **Walker semantics have a learning curve.** Plugin authors learn `visit` / `prepend` / `here`.
- **Index design is per-Node.** Compound indexes must be declared on each subclass (`@compound_index`).
- **Cascading deletes traverse edges**, which is slower than a SQL `ON DELETE CASCADE` for large fan-out — but with bounded conversation sizes, this is acceptable.

## Alternatives considered

1. **Plain relational schema** — rejected: brittle plugin extension, migration overhead.
2. **Document DB (raw)** — rejected: loses the walker abstraction and edge semantics jvspatial provides.
3. **Event-sourced log + projection** — rejected: complexity overhead; jvagent's writes are not append-only-friendly (conversations get pruned).

## References

- [`SPEC.md`](../SPEC.md) §2 — graph hierarchy
- [`jvspatial-integration.md`](../reference/jvspatial-integration.md) — what jvspatial provides
- [`adr/0006-jvspatial-dependency.md`](0006-jvspatial-dependency.md) — why jvspatial as a separate library
