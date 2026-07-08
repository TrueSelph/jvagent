# ADR 0028 — Rails orchestration deprecation

**Status**: Superseded by [0029-rails-orchestration-removal.md](0029-rails-orchestration-removal.md) (removed in 0.1.1)  
**Date**: 2026-07-07  
**Related**: [0012-skill-executive-architecture.md](0012-skill-executive-architecture.md), [docs/orchestration-modes.md](../../docs/orchestration-modes.md)

---

## Context

jvagent originally shipped a **Rails** orchestration pattern: `InteractRouter` at weight `-200` classifies intent via Chain-of-Verification LLM prompting, then visits retrieval/converse InteractActions that inject directives for `ReplyAction`. ADR-0012 introduced the **Orchestrator** pattern at the same weight slot — tool selection replaces intent routing; skills and action tools surface capabilities directly.

Both patterns remain loadable for backward compatibility. New agents and profiles default to Orchestrator-only. Maintaining two routing models increases test surface, documentation drift, and operator confusion (especially the "never enable both at `-200`" rule).

---

## Decision

1. **Orchestrator is the sole recommended pattern** for new deployments (already documented in `docs/orchestration-modes.md`).
2. **Rails components are deprecated**, not removed in this ADR:
   - `InteractRouter` (`jvagent/interact_router`)
   - Legacy-compat directive IAs: `RetrievalInteractAction`, `ConverseInteractAction`, `WebSearchRetrievalInteractAction`, `UserLongMemoryRetrievalInteractAction`, and related retrieval wrappers
3. **Removal target: jvagent 0.2.0** — same milestone as other deprecated APIs (`User.user_model`, `get_dispatch_visitor()`, etc.). Until then, Rails actions continue to load and execute for existing `agent.yaml` configurations.
4. **No runtime logger spam on every turn** — deprecation is communicated via class/module docstrings, `actions-catalog.md`, CHANGELOG, and this ADR. Optional `on_startup` warnings are reserved for a future release if telemetry shows sustained Rails usage.

---

## Migration

1. Remove `jvagent/interact_router` from `agent.yaml`.
2. Ensure `jvagent/orchestrator` and `jvagent/reply` are present.
3. Replace retrieval/converse IAs with Orchestrator skills that call `pageindex`, `web_fetch`, MCP tools, or domain action tools explicitly.
4. Run `jvagent path/to/app validate` and orchestrator E2E / CUCS scenarios.

See [docs/deprecated-api-migration.md](../../docs/deprecated-api-migration.md).

---

## Consequences

- **Positive**: Single mental model for new contributors; reduced dual-routing test matrix over time.
- **Negative**: Long-tail agents on Rails need a planned migration before 0.2.0.
- **Neutral**: Code remains until 0.2.0; deprecation is documentation-first plus class-level notices.
