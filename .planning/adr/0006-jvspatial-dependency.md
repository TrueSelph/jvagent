# ADR 0006 — Build jvagent on a separate jvspatial framework

**Status**: Accepted
**Date**: foundational

## Context

jvagent needs:

- An async, entity-centric, graph-aware persistence layer.
- A walker abstraction for declarative traversal.
- HTTP server infrastructure with auth, file storage, and per-request DB context.
- Multiple persistence backends (JSON for dev, MongoDB / DynamoDB for prod).

These primitives are not jvagent-specific. They are reusable for any graph-shaped application.

## Decision

Extract the graph primitives into a separate library, **jvspatial**, and depend on it via pip:

```toml
# pyproject.toml
jvspatial>=0.0.7
```

jvspatial owns:

- `Object`, `Node`, `Edge`, `Walker`, `Root` entity hierarchy.
- Database adapters (`JSON`, `SQLite`, `MongoDB`, `DynamoDB`).
- `@attribute`, `@compound_index`, `@on_visit`, `@endpoint` decorators.
- FastAPI integration (`Server`, JWT auth, OpenAPI docs).
- File storage abstractions (local, S3).
- Per-event-loop graph context.

jvagent owns:

- The `Action` / `InteractAction` contract.
- `App` / `Agent` / `Memory` / `User` / `Conversation` / `Interaction` node types.
- The interact subsystem and response bus.
- The Executive pattern + skill system.
- Plugin discovery and namespace rules.

## Consequences

### Positive
- **Reuse.** jvspatial can host non-jvagent applications.
- **Independent release cycle.** Each library versions on its own cadence.
- **Forces clean boundaries.** jvagent code that wants to mutate persistence-layer behavior has to upstream it to jvspatial, encouraging principled changes.
- **Smaller surface per library.** Easier to test in isolation.

### Negative
- **Two repos / two release pipelines.** Coordinating breaking changes adds overhead.
- **Version-pin drift risk.** jvagent must specify a floor and test against it.
- **Some abstractions leak.** jvagent occasionally needs jvspatial internals (e.g., `_endpoint_registry` for action endpoint unregister at [`action/base.py:370`](../../jvagent/action/base.py)). These are friction points; ideally jvspatial exposes them as stable APIs over time.

## Version policy

- **Floor**: declared in [`pyproject.toml`](../../pyproject.toml) as `jvspatial>=X.Y.Z`. Current: `>=0.0.7`. Tested with `0.0.8`.
- **Bump floor** when jvagent starts depending on a feature only available in a newer jvspatial.
- **Compatibility shims** live in [`jvagent/core/jvspatial_compat.py`](../../jvagent/core/jvspatial_compat.py).
- jvspatial is on **SemVer (0.x)**. Until 1.0, minor versions may break — pin and test.

## Alternatives considered

1. **Vendor jvspatial inside jvagent.** Rejected: forks a copy, kills reuse, doubles maintenance.
2. **Build graph primitives directly into jvagent.** Rejected: rapid jvagent iteration would constantly perturb the graph layer.
3. **Use an existing graph library (e.g., NetworkX, py2neo).** Rejected: those don't provide async persistence with multi-backend, FastAPI integration, and walker semantics in one package.

## What this means for jvagent contributors

- Don't patch jvspatial from inside jvagent. File a jvspatial issue / PR.
- When adding a feature that needs jvspatial changes, sequence: jvspatial PR → jvspatial release → bump pin in jvagent → jvagent PR.
- Read jvspatial's [`SPEC.md`](../../../jvspatial/SPEC.md) when working close to the graph layer. The reading-list table in [`jvspatial-integration.md`](../reference/jvspatial-integration.md) is ordered by depth.

## References

- [`jvspatial-integration.md`](../reference/jvspatial-integration.md) — the boundary doc.
- jvspatial own docs: `../../jvspatial/README.md`, `../../jvspatial/SPEC.md`.
