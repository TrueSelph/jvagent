# ADR 0029 — Rails orchestration and long memory removal

**Status**: Accepted  
**Date**: 2026-07-08  
**Supersedes**: [0028-rails-orchestration-deprecation.md](0028-rails-orchestration-deprecation.md)

---

## Decision

Remove in jvagent **0.1.1**:

1. Rails orchestration: `InteractRouter`, directive IAs (retrieval, converse, web search, pageindex retrieval, long memory retrieval).
2. UserLongMemory subsystem: graph nodes, background writer, PageIndex store IA, `LongMemoryService`.
3. Deprecated APIs: `User.user_model`, `get_dispatch_visitor()`, `skills_source=registry`, `include_legacy_agent_skills`.

Orchestrator + `User.memory` + PageIndexAction tools are the sole supported paths.

---

## Consequences

- Breaking change for downstream `agent.yaml` files still on Rails or long-memory actions.
- Orphaned DB nodes (`UserLongMemory*`, `user_long_memory_{user_id}` PageIndex docs) remain but are unreachable; no automatic purge in 0.1.1.
- PageIndex jvforge webhook URL path unchanged for external clients.
