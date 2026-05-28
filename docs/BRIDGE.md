# Bridge Architecture

## Overview

Bridge is the canonical multi-helm deployment pattern. Bridge composes **N helms** behind a single `InteractAction` slot at weight `-200` and orchestrates **shifts** between them as walker hops. Each helm is itself an `Action` with a `step(visitor, bridge_state) → HelmStepResult` contract.

The pattern targets latency-sensitive UX (voice, fast chat) and mixed workloads where trivial turns shouldn't pay the cost of a heavy reasoner. A fast classifier helm (Reflex) handles smalltalk in <300ms; deliberate turns shift to a Reasoning helm; delivery polish runs through `PersonaAction` via `EMIT(via_persona=True)`. Each shift is observable, streamable, and AccessControl-gated.

Bridge is the only pattern-orchestrator that ships with jvagent today. The earlier monolithic `CockpitInteractAction` was removed in May 2026 — see [`.planning/COCKPIT-SUNSET.md`](../.planning/COCKPIT-SUNSET.md) for the migration. See [`PATTERNS.md`](../.planning/PATTERNS.md) for when to use Rails (single `InteractRouter` + per-IA chain) vs Bridge.

## Architecture

```
POST /agents/{agent_id}/interact
  │
  ├─ 1. Bootstrap (InteractWalker)
  │     └─ Resolve User → Conversation → Interaction
  │
  ├─ 2. Curate walker queue (once per turn)
  │     └─ Bridge owns walker queue mutations; always_execute IAs preserved
  │
  └─ 3. BridgeInteractAction (walker-revisit pattern)
        │
        ├─ First visit: resolve current_helm (default_helm or first in helms[])
        │   └─ Record initial ShiftRecord(from=None, to=current_helm)
        │
        └─ Each walker visit: helm.step(visitor, bridge_state)
            │
            ├─ EMIT(finalize=True)            → publish, clear state, end turn
            ├─ EMIT(finalize=False)           → publish, re-enqueue helm
            ├─ CONTINUE                       → re-enqueue helm (helm dispatched its own tools)
            ├─ SHIFT(target)                  → AC-check, ack-on-shift, switch helm, re-enqueue
            ├─ DELEGATE(action, follow_up=F)  → AC-check, run rails IA inline, persona-finalize, end turn
            ├─ DELEGATE(action, follow_up=T)  → AC-check, run rails IA inline, re-enqueue (chain mode)
            └─ YIELD                          → clear state, let walker continue weight chain
```

Bridge invariants:

1. **One model call per walker visit** (ADR-0002 preserved).
2. **Bridge owns walker queue mutations** — helms never call `visitor.prepend([self])` directly.
3. **Helms are invisible to the walker's action trace** — only `BridgeInteractAction` visits are recorded.
4. **Shift budget per turn** (default `4`) prevents ping-pong loops.
5. **First-emit timeout** (default `800ms`) triggers a safety-net ack on long deliberate turns.

State persists on `visitor._bridge_state` (`BridgeState` dataclass). Per-helm internal state (e.g. ReasoningHelm's session state) lives on `visitor._skill_state`, scoped by the helm that owns the current visit.

## Helms

A helm is a `BaseHelm` subclass — an `Action` that implements `step()` and declares a manifest. Helms are connected to the agent like any other action and named on the bridge's `helms:` list.

| Helm | Class | Latency class | Purpose |
|---|---|---|---|
| **ReflexHelm** | `jvagent.action.helm.reflex.ReflexHelm` | `fast` | Sub-500ms classifier. Handles greetings, smalltalk, simple acknowledgements directly via a small completion model; SHIFTs to a deliberate helm on any utterance that needs reasoning. |
| **ReasoningHelm** | `jvagent.action.helm.reasoning.ReasoningHelm` | `deliberate` | Engine-style think-act-observe loop. One model call per walker visit; full tool surface (memory, response, task, conversation, skill, artifact, search). |
| **Specialist** | any rails `InteractAction` | (manifest) | Not a helm — invoked via `DELEGATE(action=...)`. Lets Bridge yield cleanly to a deterministic rails IA for an in-progress workflow (e.g. interview, form). |

**Persona stylisation** is not a helm. It lives in `PersonaAction` and is invoked by Bridge via `EMIT(via_persona=True)` → `BridgeInteractAction._publish_emit_via_persona` → `PersonaAction.respond`. A helm that wants its final output stylised returns `EMIT(text=..., via_persona=True, finalize=True)` and Bridge handles the rest. The originally-planned dedicated `PersonaHelm` was scrapped in May 2026 (see [`adr/0007`](../.planning/adr/0007-bridge-helm-architecture.md) accepted-state amendments).

### HelmStepResult verb set (v0.2)

```
EMIT        publish text; finalize ends turn, non-finalize re-enqueues
CONTINUE    re-enqueue with no Bridge-side state mutation (helm dispatched its own tools)
SHIFT       switch to target helm; ack-on-shift if target is deliberate/long
DELEGATE    resolve rails InteractAction, run inline; follow_up=False ends turn,
            follow_up=True re-enqueues for the next IA in a chain
YIELD       exit Bridge cleanly; walker continues weight chain
```

Verb semantics live in [`jvagent/action/helm/contracts.py`](../jvagent/action/helm/contracts.py).

Revision history:
- **v0** — original set: `EMIT | EXECUTE | SHIFT | DELEGATE | YIELD`.
- **v0.1** — additive: `CONTINUE` for helms that dispatch their own tools; `DELEGATE.follow_up` for multi-IA chains.
- **v0.2** — breaking cleanup: `EXECUTE` removed (no helm used it); `SHIFT.interrupt` removed (Bridge always auto-DELEGATEs on turn-lock; lock-breaking lives in the IA's intent classifier).

### Shift verbs and turn-lock

- Every `SHIFT`, `DELEGATE`, turn-lock auto-delegate, and initial helm pick records a `ShiftRecord` on `BridgeState.shift_log`.
- A `SHIFT` to a helm whose `manifest.latency_class` is `deliberate` or `long` emits a `transient_ack` text via the response bus (default: "Working on it…") before the target helm gets its first visit.
- Turn-lock: when an action with `manifest.turn_lock=True` is mid-workflow in the recent interaction history, Bridge **always** auto-`DELEGATE`s the next utterance to that owner — no helm-level escape. Lock-breaking lives in the rails IA's own intent classifier (e.g. an interview's CANCELLATION intent reading `manifest.interrupt_phrases`).

## Pattern-agnostic primitives

These primitives live at harness level and are usable by other patterns:

- **Manifest schema** ([`jvagent/action/manifest.py`](../jvagent/action/manifest.py)) — `latency_class`, `turn_lock`, `interrupt_phrases`, `pattern_compatibility` fields on any `Action` package. Exposed via `Action.get_manifest()`.
- **`helm_shift` observability event** ([`docs/logging.md`](logging.md)) — appended to `Interaction.observability_metrics` for every helm transition with a `routing_source` label (`initial` | `turn_lock` | `helm_shift` | `helm_delegate`).
- **Bridge observability bundle** — `Interaction.parameters["bridge_observability"]` carries `shift_log`, `helm_timings_seconds`, `helm_step_counts`, `shift_count`, `turn_started_at`, `last_emit_at` for the turn.

## Plugging Into the Interact Pipeline

`BridgeInteractAction` is a standard `InteractAction` (weight: `-200`) that plugs into the `InteractWalker` pipeline. Minimal `agent.yaml`:

```yaml
actions:
  - action: jvagent/bridge
    context:
      enabled: true
      helms:
        - ReflexHelm
        - ReasoningHelm
      default_helm: ReflexHelm
      shift_budget_per_turn: 4
      first_emit_timeout_ms: 800

  - action: jvagent/reflex_helm
    context:
      enabled: true
      model: gpt-4o-mini
      model_action_type: OpenAILanguageModelAction
      default_shift_target: ReasoningHelm

  - action: jvagent/reasoning_helm
    context:
      enabled: true
      model: gpt-4o-mini
      model_action_type: OpenAILanguageModelAction
      enable_canned_response: false   # Reflex owns user-facing canned/ack
```

The interact pipeline (`InteractWalker` → `Actions` → sorted `InteractAction` chain by weight) is fully preserved. Bridge owns the pattern-orchestrator slot at weight `-200`.

## Action Configuration

### Recipe 1 — Default conversational agent (Reflex + Reasoning)

```yaml
- action: jvagent/bridge
  context:
    enabled: true
    helms: [ReflexHelm, ReasoningHelm]
    default_helm: ReflexHelm

- action: jvagent/reflex_helm
  context:
    enabled: true
    model: gpt-4o-mini
    model_action_type: OpenAILanguageModelAction
    timeout_seconds: 3.0
    default_shift_target: ReasoningHelm
    can_emit_directly: true

- action: jvagent/reasoning_helm
  context:
    enabled: true
    model: gpt-4o-mini
    model_action_type: OpenAILanguageModelAction
    max_iterations: 25
    tool_tier: standard
    skills_source: both
    enable_canned_response: false
```

### Recipe 2 — Reflex on a faster provider (Groq / Cerebras)

```yaml
- action: jvagent/groq_lm
  context:
    enabled: true
    model: llama-3.1-8b-instant

- action: jvagent/reflex_helm
  context:
    enabled: true
    model: llama-3.1-8b-instant
    model_action_type: GroqLanguageModelAction
    timeout_seconds: 1.5
```

The classifier prompt is ~800 prompt tokens. A genuinely fast provider drops trivial-turn p50 from ~2.1s to ~1.0s, closing on the milestone-J 30% target.

### Recipe 3 — Add persona stylisation to ReasoningHelm output

Persona stylisation is not a helm — it lives in `PersonaAction` and is dispatched by Bridge when a helm returns `EMIT(via_persona=True)`. Install `PersonaAction` on the agent, configure its `persona_name`, `persona_description`, and `persona_capabilities`, and ReasoningHelm will automatically request stylisation on its final EMIT.

```yaml
- action: jvagent/bridge
  context:
    enabled: true
    helms: [ReflexHelm, ReasoningHelm]
    default_helm: ReflexHelm

- action: jvagent/persona
  context:
    enabled: true
    persona_name: Research Assistant
    persona_description: >-
      You are a knowledgeable research assistant…
    persona_capabilities:
      - Answer questions from internal knowledge base
      - Search the web for current information
```

How it works:

1. ReasoningHelm finishes its engine loop and returns `EMIT(text=<raw>, finalize=True, via_persona=True, …)`.
2. Bridge's `_handle_emit` sees `via_persona=True` and routes through `_publish_emit_via_persona`.
3. `PersonaAction.respond` produces the stylised output using its configured directives and publishes on the bus.
4. The turn closes.

The originally-planned dedicated `PersonaHelm` (an extra hop via `SHIFT(target=PersonaHelm)`) was scrapped in May 2026 — the `EMIT(via_persona=True)` path is functionally equivalent without consuming a shift-budget slot. See [`adr/0007`](../.planning/adr/0007-bridge-helm-architecture.md) accepted-state amendments.

### Recipe 4 — Hardened production posture

```yaml
- action: jvagent/bridge
  context:
    enabled: true
    helms: [ReflexHelm, ReasoningHelm]
    default_helm: ReflexHelm
    shift_budget_per_turn: 3            # tighter
    first_emit_timeout_ms: 600          # earlier safety-net ack
    safety_net_ack_text: "Still working…"
    denied_response_text: "Sorry, I can't do that here."

- action: jvagent/reasoning_helm
  context:
    enabled: true
    block_raw_tool_invocation: true
    stream_internal_progress: false
    sanitize_tool_errors: true
```

### Configuration groups (cheat sheet)

| Group | Tunable when … |
|---|---|
| Bridge orchestration | `helms`, `default_helm`, `shift_budget_per_turn`, `first_emit_timeout_ms` |
| Reflex classifier | Provider / model, `timeout_seconds`, `default_shift_target`, `can_emit_directly` |
| Reasoning engine | Router + engine + skills + tool tier (memory, response, task, conversation, skill, artifact, search) |
| Persona stylisation | `PersonaAction` config (`persona_name`, `persona_description`, `persona_capabilities`); helms invoke it via `EMIT(via_persona=True)` |
| Safety nets | `safety_net_ack_text`, `denied_response_text`, `enable_transient_ack`, `block_raw_tool_invocation` |

### Gotchas

- `ReasoningHelm` does not accept the legacy monolithic surfaces (`enable_canned_response`, `canned_response_max_words`, `skip_canned_for_intents`, `converse_enabled`, `converse_context_limit`, `converse_persona_prompt`, `conversational_fast_path`, `enable_router_preclassifier`, `clarify_response_prompt`). Reflex owns transient_ack/smalltalk, Bridge owns persona delivery, and the router prompt no longer carries a posture surface (see [ADR-0008](../.planning/adr/0008-router-unification.md)). Including any of them in `agent.yaml` triggers an unknown-context-key warning at startup and the value is silently dropped.
- **Dispatch regimes.** ReasoningHelm classifies each turn into one of four regimes after capability decode: `SKILLS_ONLY` (engine runs with skill-loop guidance), `IAS_ONLY` (engine LM call SKIPPED — DELEGATE chain runs and yields, saving 800–1500 ms), `MIXED` (engine runs with IA-chain-awareness, then DELEGATE chain), `NONE` (engine runs with bare persona prompt). The regime is recorded on the `helm_shift` observability event as `dispatch_regime` so operators can filter logs. See [ADR-0008](../.planning/adr/0008-router-unification.md).
- ReflexHelm's classifier uses temperature `0.0` and a small `max_tokens` (256) by design. Don't raise these — the helm is meant to be a deterministic gate, not a generator.
- Recap / recall questions ALWAYS SHIFT from ReflexHelm to ReasoningHelm regardless of perceived history. The reflex prompt enforces this explicitly.
- **Locale-static strings.** A few Bridge / helm attributes are STATIC strings that don't adapt to the user's language: `safety_net_ack_text` and `denied_response_text` on Bridge, and `fallback_text` / `tool_invocation_refusal_text` on ReflexHelm. The Bridge `safety_net_ack_text` defaults to `"…"` (universal) so multilingual deployments inherit a safe placeholder; the rest default to English. Override per agent.yaml for single-language deployments, or use a channel adapter that localises before publish. Dynamic strings (Reflex's `transient_ack`, persona-rendered EMITs) DO adapt — they go through model calls that read `detected_language`.
- **Tool-injection defense is on by default.** `ReflexHelm.block_raw_tool_invocation` and `ReasoningHelm.block_raw_tool_invocation` both default to `true` — canonical tool-call syntax in user utterances is refused at Layer 1 (Reflex regex) and substring-matched named tools are refused at Layer 2 (Engine pre-dispatch). Don't disable in production without operator review.

## Module Structure

```
jvagent/action/bridge/                  # Orchestrator
  ├─ bridge_interact_action.py          # BridgeInteractAction (weight -200)
  ├─ state.py                           # BridgeState dataclass
  ├─ access.py                          # tool:helm:* and tool:delegate:* AC
  ├─ turn_lock.py                       # find_turn_lock_owner
  └─ info.yaml

jvagent/action/helm/                    # Helm primitives + concrete helms
  ├─ base.py                            # BaseHelm (publish, publish_thought, respond)
  ├─ contracts.py                       # HelmStepResult verb set + dataclasses
  ├─ stub_helm.py                       # Test fixture
  ├─ reflex/
  │   ├─ reflex_helm.py                 # Fast classifier helm
  │   ├─ prompts.py
  │   └─ info.yaml
  └─ reasoning/                         # ReasoningHelm package
      ├─ reasoning_helm.py
      ├─ engine.py                      # Engine (think-act-observe loop)
      ├─ routing/router.py              # EngineRouter (unified-capability classifier)
      ├─ routing/types.py               # CapabilityRef, DispatchRegime,
      │                                 #   DispatchPlan, RoutingResult
      ├─ routing/prompts.py             # Single CAPABILITIES AVAILABLE catalog
      ├─ catalog/                       # SkillCatalog, ActionResolver
      ├─ delivery/                      # gates, helpers, delegation
      ├─ registry/                      # tool assembler, access, visitor shim
      ├─ tools/                         # response, memory, task, skill, …
      └─ info.yaml

# Persona stylisation lives in jvagent/action/persona/ (PersonaAction) and is
# invoked by Bridge via EMIT(via_persona=True). The previously planned
# jvagent/action/helm/persona/ helm package was scrapped in May 2026.

jvagent/action/manifest.py              # Pattern-agnostic Manifest schema
```

ReasoningHelm was originally forked from the (now-removed) `CockpitInteractAction` engine and has since diverged. See [`.planning/COCKPIT-SUNSET.md`](../.planning/COCKPIT-SUNSET.md) for the May 2026 sunset of the cockpit pattern.

## Implementing a New Helm

Helms are `BaseHelm` subclasses. The minimum surface:

```python
from jvspatial.core.annotations import attribute

from jvagent.action.helm.base import BaseHelm
from jvagent.action.helm.contracts import EMIT, SHIFT, YIELD, HelmStepResult


class MyHelm(BaseHelm):
    enabled: bool = attribute(default=True)

    @classmethod
    def helm_name(cls) -> str:
        return "MyHelm"

    async def step(self, visitor, bridge_state) -> HelmStepResult:
        utterance = (visitor.utterance or "").strip()
        if not utterance:
            return YIELD()
        if utterance.lower() in ("hi", "hello"):
            return EMIT(text="Hey.", finalize=True)
        return SHIFT(target="ReasoningHelm", reason="not a greeting")
```

Then declare the helm in `info.yaml`:

```yaml
package:
  name: jvagent/my_helm
  archetype: MyHelm
  manifest:
    latency_class: fast
    turn_lock: false
    pattern_compatibility:
      - bridge
```

And add it to the bridge's `helms:` list in `agent.yaml`. AccessControl resources for the helm are auto-derived as `tool:helm:MyHelm`.

## Observability

Bridge stamps the following on each `Interaction`:

| Surface | Key | Content |
|---|---|---|
| `observability_metrics` | `events[].HELM_SHIFT` | One event per shift, with `from`, `to`, `reason`, `ack_emitted`, `at_monotonic` |
| `parameters` | `bridge_shift_log` | Full `ShiftRecord` list serialized |
| `parameters` | `bridge_helm_timings` | `{helm_name: total_wall_seconds}` |
| `parameters` | `bridge_helm_step_counts` | `{helm_name: visit_count}` |

See [`docs/logging.md`](logging.md) → "Bridge Observability" for query examples.

## References

- [`.planning/PATTERNS.md`](../.planning/PATTERNS.md) — pattern catalog + performance ledger
- [`.planning/adr/0007-bridge-helm-architecture.md`](../.planning/adr/0007-bridge-helm-architecture.md) — Bridge + Helm architecture decision
- [`.planning/BRIDGE-ROADMAP.md`](../.planning/BRIDGE-ROADMAP.md) — build plan
- [`docs/logging.md`](logging.md) — observability schema (Bridge events)
