# Patterns

Catalog of deployment patterns supported by jvagent. Each pattern is a *composition* of harness primitives (`InteractAction`, `InteractWalker`, `response_bus`, `AccessControlAction`) — the harness itself is pattern-agnostic per [`SPEC.md`](SPEC.md).

> **Live document.** Performance ledger updated after each pattern-matrix smoke run. See [`BRIDGE-ROADMAP.md`](BRIDGE-ROADMAP.md) milestone J.

---

## Supported patterns

### Rails

- **Profile**: `minimal`
- **Composition**: Pure `InteractAction` chain. No model agency. Walker visits each action in weight order; each action runs deterministic logic.
- **State**: per-action attributes, `Conversation.context`, `Interaction.parameters`.
- **Use when**: Deterministic flows (channel adapters, scripted forms, gated processes). Latency-critical paths with fixed branching. Compliance contexts where every step must be auditable.
- **Avoid when**: User input is open-ended or requires intent classification.
- **Status**: First-class. Supported indefinitely.

### Cockpit

- **Profile**: `cockpit` (current scaffolder default)
- **Composition**: Single-helm model agency on the walker-revisit substrate ([`adr/0002`](adr/0002-walker-revisit-cockpit.md)). `CockpitInteractAction` runs Phase 1 (`CockpitRouter` classifier) and Phase 2 (`CockpitEngine.step()` loop).
- **State**: `visitor._skill_state` across walker revisits.
- **Use when**: Conversational agents with broad skill/tool surface. Research and exploration tasks. Today's default — well-understood, battle-tested.
- **Avoid when**: Sub-500ms first-response latency is a hard requirement on trivial turns (every turn pays the heavy-model cost).
- **Status**: First-class. **No deprecation planned.** Cockpit remains the scaffolder default through and beyond Bridge milestone K.

### Bridge

- **Profile**: `bridge` (new at milestone K)
- **Composition**: `BridgeInteractAction` orchestrates N helms (Reflex, Reasoning, Specialist, Persona). Helms shift via the verb set in [`adr/0007`](adr/0007-bridge-helm-architecture.md). Each shift is a walker hop — observable, streamable, AC-controllable.
- **State**: `visitor._bridge_state` (`BridgeState` dataclass).
- **Use when**: Latency-sensitive UX (voice, fast chat); mixed workloads where some turns are trivial and others are heavy; deployments that benefit from peer-level delegation to rails IAs without baking it into a single agent.
- **Avoid when**: A single reasoning loop already meets latency targets and the operational complexity of multiple helms is not justified.
- **Status**: Ships at K alongside Cockpit. Deprecation of Cockpit, if it ever happens, requires data from the performance ledger and an explicit ADR.

---

## Decision tree

```
Open-ended user input requiring reasoning or tool use?
├─ No  → Rails
└─ Yes
   ├─ Sub-500ms first-response on trivial turns is a requirement?
   │   ├─ No  → Cockpit
   │   └─ Yes → Bridge
   ├─ Mixed workload (some turns trivial, some deliberate, some delegated to rails IAs)?
   │   ├─ No  → Cockpit
   │   └─ Yes → Bridge
   └─ Operator wants multi-model composition (fast classifier + heavy reasoner + persona polish) as first-class shifts?
       ├─ No  → Cockpit
       └─ Yes → Bridge
```

---

## Pattern coexistence

All three patterns share the same harness primitives:

| Primitive | Rails | Cockpit | Bridge |
|---|---|---|---|
| `InteractWalker` | ✓ | ✓ | ✓ |
| `InteractAction` weight ordering | ✓ | ✓ | ✓ |
| `visitor.prepend([self])` revisit | — | ✓ (engine loop) | ✓ (helm loop) |
| `response_bus` | ✓ | ✓ | ✓ |
| `AccessControlAction` | ✓ | ✓ | ✓ (extended taxonomy) |
| `Conversation` / `Interaction` chain | ✓ | ✓ | ✓ |
| One model call per walker visit | n/a | ✓ | ✓ |

A single agent CAN mix patterns: e.g., a Rails-style auth IA (weight `-1000`) ahead of a Bridge IA (weight `-200`). Bridge and Cockpit installed simultaneously in the same agent is not supported and the scaffolder rejects it at validate-time — they occupy the same weight slot and operator intent is ambiguous.

---

## Performance ledger

Empirical comparison published per the 6-utterance baseline suite ([`BRIDGE-ROADMAP.md`](BRIDGE-ROADMAP.md) §Baseline). Baseline commit: `7d95904`.

| Pattern config | Median total dur(s) | p99 dur(s) | Total tokens | Trivial-turn p50(s) | Notes |
|---|---|---|---|---|---|
| Cockpit (control) | 33.15 | — | 34094 | — | Baseline from commit `7d95904` |
| Bridge + Reasoning | — | — | — | — | TBD at milestone C parity gate |
| Bridge + Reflex + Reasoning | — | — | — | — | TBD at milestone J |
| Bridge + Reflex + Reasoning + Persona | — | — | — | — | TBD at milestone J |
| Bridge + Reflex + Reasoning + Specialist | — | — | — | — | TBD at milestone J |

**Deprecation policy:** A pattern moves from supported → deprecated only when:

1. Another pattern dominates the ledger for the deprecated pattern's target use case across **two or more measurement cycles**, AND
2. An explicit ADR (ADR-0008+) proposes the deprecation with a documented migration path.

---

## Pattern compatibility for action authors

Authors of new `Action` / `InteractAction` packages should mark pattern compatibility in `info.yaml`:

```yaml
package:
  name: namespace/action_name
manifest:
  purpose: "..."
  latency_class: quick
  pattern_compatibility:               # informational at v0
    - rails
    - cockpit
    - bridge
```

The harness does not enforce this at v0 — it is consumed by tooling (validators, scaffold checks) and by helms for prompt assembly (Bridge's Reflex builds peer-awareness lists from manifests).

---

## References

- [`SPEC.md`](SPEC.md) — normative harness contract (pattern-agnostic)
- [`adr/0002-walker-revisit-cockpit.md`](adr/0002-walker-revisit-cockpit.md) — Cockpit walker-revisit
- [`adr/0007-bridge-helm-architecture.md`](adr/0007-bridge-helm-architecture.md) — Bridge + Helm architecture
- [`BRIDGE-ROADMAP.md`](BRIDGE-ROADMAP.md) — Bridge build plan
- [`COCKPIT-ROADMAP.md`](COCKPIT-ROADMAP.md) — Cockpit build history
- [`docs/COCKPIT.md`](../docs/COCKPIT.md) — Cockpit user reference
