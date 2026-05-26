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

Run the matrix harness to refresh measurements:

```bash
# Enable both bridge_agent and cockpit_agent in app.yaml, then:
.venv/bin/python tests/action/bridge/smoke_pattern_matrix.py \
    --agents bridge_agent cockpit_agent \
    --label rR_vs_cockpit
```

JSON archives land under `tests/action/bridge/baselines/matrix_<label>_<sha>.json`.

### Cockpit baseline (control)

| Metric | Value | Notes |
|---|---|---|
| Total dur(s) | 33.15 | Archived at commit `7d95904` (original) |
| Total dur(s) | 22.77 | Re-baseline at `b830f42` (fresh matrix run, gpt-4o-mini + gpt-4.1) |
| Total tokens | 34094 / 23077 | Original / fresh |
| Trivial-turn p50(s) | 2.12 | Fresh, gpt-4o-mini |
| p99 dur(s) | 7.90 | Fresh |

### Bridge configurations

Each cell records a measurement against the current `bridge_agent.yaml` composition. Toggle helms in YAML between matrix runs and copy results in.

| Pattern config | Helms in `agent.yaml` | Total dur(s) | p99 dur(s) | Total tokens | Trivial-turn p50(s) | Source archive |
|---|---|---|---|---|---|---|
| Bridge + Reasoning | `[ReasoningHelm]` | TBD | TBD | TBD | TBD | TBD |
| Bridge + Reflex + Reasoning | `[ReflexHelm, ReasoningHelm]` | 29.25 | 8.69 | 20805 | 2.27 | [`matrix_j_initial_b830f42.json`](../tests/action/bridge/baselines/matrix_j_initial_b830f42.json) |
| Bridge + Reflex + Reasoning + Persona | `[ReflexHelm, ReasoningHelm, PersonaHelm]` | TBD | TBD | TBD | TBD | TBD |
| Bridge + Reflex + Reasoning + Specialist | `[ReflexHelm, ReasoningHelm]` + Interview IA in chain | TBD | TBD | TBD | TBD | TBD |

### Headline findings (first matrix run, OpenAI gpt-4o-mini Reflex)

vs Cockpit (fresh re-baseline):

- **Total tokens**: Bridge **-10%** (20805 vs 23077). Reflex's classifier prompt is smaller than Cockpit's router+converse combined for trivial turns.
- **Total dur(s)**: Bridge **+28%** (29.25 vs 22.77). Reflex's classifier adds a network round-trip on every turn that Cockpit's preclassifier short-circuits on smalltalk.
- **Trivial-turn p50**: Bridge **+7%** (2.27 vs 2.12). Below the 30% reduction target from the J exit gate.
- **p99 dur(s)**: Bridge **+10%** (8.69 vs 7.90). Within tolerance.

The 30% trivial-turn-latency target was predicated on Reflex running on a genuinely faster provider (Groq `llama-3.1-8b-instant` at ~200ms or Cerebras). The current OpenAI gpt-4o-mini Reflex is **comparable** to Cockpit's converse persona call, so the Bridge wrapper cost (router + classifier round-trip) is paid without a compensating provider speedup.

### Reflex provider swap roadmap

To hit the J exit gate:

1. Add a Groq / Cerebras `LanguageModelAction` to the agent.
2. Override `reflex_helm.model_action_type` to point at it.
3. Re-run the matrix.

The classifier's prompt is small enough (~800 prompt tokens) that even a fast provider should keep cost reasonable. Wall-clock should drop ~1s per turn → trivial-turn p50 lands around 1.0-1.2s, ~50% reduction vs Cockpit.

### Ack-on-shift UX win (not captured in aggregate metrics)

The matrix harness measures **end-to-end turn duration**. The architectural win of Bridge — visible "Working on it." within 1-2s on deliberate turns vs Cockpit's silent wait for the engine — is a **time-to-first-byte (TTFB)** improvement that aggregate timings don't expose. A J+1 follow-up could add TTFB instrumentation to the matrix harness; for now the UX win is documented qualitatively from the live browser smoke (see commit `f8fa0ab`).

### Exit-gate targets for milestone J

- **Median latency reduction ≥30% on trivial turns** (`greeting`, `informational_simple`, `thanks_followup`) for Bridge + Reflex + Reasoning vs Cockpit.
- **p99 latency not worse than baseline** for any Bridge cell.
- **Total tokens not worse than baseline** for any Bridge cell.
- **All five configs execute the 6-utterance suite without errors.**

### Notes on measurement variance

- LM-output metrics (`response_chars`, `duration_s`) are inherently non-deterministic. Run each cell 3× and record the median to smooth variance, or accept ±10% per-cell drift between runs.
- Re-baseline `cockpit_agent` alongside each Bridge cell so the comparison is taken under the same OpenAI conditions.
- Total tokens are usually more stable than wall-clock; treat them as the primary efficiency metric.

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
