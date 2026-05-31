# Patterns

Catalog of deployment patterns supported by jvagent. Each pattern is a *composition* of harness primitives (`InteractAction`, `InteractWalker`, `response_bus`, `AccessControlAction`) — the harness itself is pattern-agnostic per [`SPEC.md`](SPEC.md).

---

## Supported patterns

### Rails

- **Profile**: `minimal`, `conversational`, `research`, `whatsapp_voice`
- **Composition**: Pure `InteractAction` chain. Walker visits each action in weight order; each action runs its own logic. An optional `InteractRouter` (weight `-200`) classifies intent and curates the walk path so anchored IAs run only when relevant.
- **State**: per-action attributes, `Conversation.context`, `Interaction.parameters`.
- **Use when**: Deterministic flows (channel adapters, scripted forms, gated processes); latency-critical paths with fixed branching; compliance contexts where every step must be auditable.
- **Avoid when**: You need a single component to hold working memory across specialist sub-tasks within a turn.
- **Status**: First-class. Supported indefinitely.

### Orchestrator

- **Profile**: `executive` (scaffolder default)
- **Composition**: `OrchestratorInteractAction` (weight `-200`) is the sole orchestrator and runs the whole turn inside one `execute()` call — no walker-revisit, no recruited centers, no separate router. Each turn it runs a bounded **think-act-observe loop** (one model call per tick) over a unified tool surface. Turn-lock is a restriction on that surface: it detects any active flow's control-task on the conversation `TaskStore`, and with `lock_active_flow` on (default) restricts the loop's callable surface to that flow's IA tool and dispatches it (no model round-trip); with it off the flow's tool is merely surfaced with a note and the model decides. **Routing is tool selection**: persona `reply`/`respond` tools, anchored IAs exposed as tools (their own `get_tools()` forwards to `execute(visitor)`; description built from the manifest `purpose` + `activates_on`), plain action tools, core tools, and skills (two specs — JV + Claude, ADR-0017) with `find_tool`/`load_tool` + `find_skill`/`use_skill` for progressive disclosure (lean tool surfacing keeps the prompt slim on large surfaces — ADR-0018). Turn-lock is deterministic (`lock_active_flow=True`) or emergent/model-mediated (`False`). See [`adr/0012-skill-executive-architecture.md`](adr/0012-skill-executive-architecture.md) (supersedes ADR-0010), [`adr/0013-togglable-deterministic-turn-lock.md`](adr/0013-togglable-deterministic-turn-lock.md), [`adr/0017-two-skill-specs-code-execution-substrate.md`](adr/0017-two-skill-specs-code-execution-substrate.md), [`adr/0018-lean-tool-surfacing.md`](adr/0018-lean-tool-surfacing.md), and [`../docs/ORCHESTRATOR.md`](../docs/ORCHESTRATOR.md).
- **State**: per-turn loop state (observations, budget); a flow's control-task (turn-lock) persisted on the conversation `TaskStore`.
- **Use when**: Conversational agents with a broad skill/tool surface that also need hardened, anchored turn-spanning flows (forms, interviews) and a single identity voice; mixed workloads where some turns are trivial and others are deliberate.
- **Avoid when**: A pure deterministic chain already meets requirements (use Rails) and no model agency is needed.
- **Status**: First-class. The scaffolder default.

---

## Decision tree

```
Open-ended user input requiring reasoning or tool use?
├─ No  → Rails
└─ Yes → Orchestrator
         (an active flow is continued deterministically by default, or
          surfaced as a tool the model may continue when lock_active_flow
          is off; a think-act-observe loop selects tools — IA-as-tools,
          action tools, persona reply/respond, core tools, and skills)
```

---

## Pattern coexistence

Both patterns share the same harness primitives:

| Primitive | Rails | Orchestrator |
|---|---|---|
| `InteractWalker` | ✓ | ✓ |
| `InteractAction` weight ordering | ✓ | ✓ |
| `response_bus` | ✓ | ✓ |
| `AccessControlAction` | ✓ | ✓ (`tool:*` / `tool:delegate:*` taxonomy) |
| `Conversation` / `Interaction` chain | ✓ | ✓ |
| One model call per tick | n/a | ✓ (loop-enforced `ModelBudget`) |

A single agent CAN mix the two: cross-cutting `always_execute` IAs (auth, intro, audit, analytics) run as ordinary weight-chain members before and after the Orchestrator at `-200`. Anchored / routable IAs are surfaced to the Orchestrator as tools (forwarding to `execute(visitor)`) rather than self-running in parallel; the model reaches them by selecting their tool.

---

## Pattern compatibility for action authors

Authors of new `Action` / `InteractAction` packages may surface routing hints in `info.yaml` via the pattern-agnostic [`Manifest`](../jvagent/action/manifest.py) block:

```yaml
package:
  name: namespace/action_name
manifest:
  purpose: "..."
  latency_class: quick
  turn_lock: false
  can_interrupt: true
```

The harness does not enforce manifest fields — they are consumed by the orchestrator (the Orchestrator surfaces anchored IAs as tools and builds each tool's description from the manifest `purpose` + `activates_on`) and by tooling (validators, scaffold checks).

---

## References

- [`SPEC.md`](SPEC.md) — normative harness contract (pattern-agnostic)
- [`adr/0012-skill-executive-architecture.md`](adr/0012-skill-executive-architecture.md) — Orchestrator architecture (supersedes ADR-0010)
- [`adr/0010-executive-centers-architecture.md`](adr/0010-executive-centers-architecture.md) — Executive + Centers architecture (superseded; history)
- [`adr/0011-skills-two-kinds.md`](adr/0011-skills-two-kinds.md) — skills as judgment over capability
- [`../docs/ORCHESTRATOR.md`](../docs/ORCHESTRATOR.md) — Orchestrator pattern reference
