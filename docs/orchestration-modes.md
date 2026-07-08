# Orchestration mode

jvagent uses a single interaction orchestration pattern: **Orchestrator** at weight `-200` with **ReplyAction** egress.

## Orchestrator (default)

| Property | Value |
|---|---|
| Primary action | `OrchestratorInteractAction` (`jvagent/orchestrator`) |
| Weight | `-200` |
| Routing | Tool selection — the model picks from the assembled tool surface |
| Egress | `ReplyAction` — single identity and response composer |
| ADRs | [0012](../.planning/adr/0012-skill-executive-architecture.md), [0013](../.planning/adr/0013-togglable-deterministic-turn-lock.md), [0014](../.planning/adr/0014-identity-on-agent-replyaction-egress.md) |

**Typical agent.yaml actions:**

- `jvagent/orchestrator`
- `jvagent/reply`
- `jvagent/intro` (optional first-message greeting)
- Domain tools: `jvagent/interview`, `jvagent/leadgen`, `jvagent/pageindex`, MCP, channels

See [ORCHESTRATOR.md](ORCHESTRATOR.md) and [thin-harness.md](thin-harness.md).

## Removed in 0.1.1

The Rails pattern (`InteractRouter`, directive retrieval/converse IAs, `UserLongMemory` graph) was removed in jvagent **0.1.1**. Use Orchestrator skills and action tools (`pageindex__search`, Serper, MCP) instead.

See [CHANGELOG.md](../CHANGELOG.md) and [ADR-0029](../.planning/adr/0029-rails-orchestration-removal.md).

## Weight ordering reference

```
IntroInteractAction          -300   (first-message parameter; always_execute)
Orchestrator                 -200
ReplyAction                  0+     (egress; Orchestrator invokes via tools/respond)
```
