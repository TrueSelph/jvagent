# ADR 0016 — Model gearing: light completion + heavy reasoning

**Status**: Accepted
**Date**: 2026-05-30
**Relation**: Extends [ADR-0012](0012-skill-executive-architecture.md) (SkillExecutive) and [ADR-0015](0015-skill-executive-configuration-surface.md) (config surface, reasoning passthrough).

---

## 1. Context

The SkillExecutive loop runs every think-act-observe tick through one model. When a reasoning model is seated, single-dimensional turns (just reply, or one tool call) pay the reasoning tax — verbosity, latency, and the truncation/`no_decision` failure modes a verbose thinking model produces. A light completion model is the right tool for simple turns; the reasoning model should engage only when the turn is genuinely multi-step across multiple tools.

## 2. Decision

Introduce **two model profiles** and **gear** between them per tick.

### 2.1 Profiles (additive; gearing is opt-in)

The existing `model` / `model_action_type` / `model_temperature` / `model_max_tokens` / `reasoning_*` become the **heavy** profile (unchanged for single-model agents). A new optional **light** profile is added: `light_model`, `light_model_action_type` (empty → same as the heavy action type), `light_model_temperature`, `light_model_max_tokens`. **Gearing engages only when both `light_model` and the main `model` are set** (it needs two distinct tiers); with no `light_model` both gears resolve to the heavy profile and behaviour is exactly as before. Reasoning kwargs are applied only on the **heavy** gear (light = completion).

**Fallback — light model, no main model.** Configuring a `light_model` with an empty main `model` is a valid single-model setup: the light model becomes the **sole** model. `_gearing_on()` is false (one effective tier), so every gear — including the `gear="light"` finalize — resolves to the light profile with reasoning off. This is forgiving by design rather than a load-time error.

`_run_model` gains a `gear` arg; `_gear_model(gear)` returns the `(action, model, temperature, max_tokens, reasoning_on)` tuple, resolving the per-gear action via `_resolve_model_action(action_type)` (so the two gears may be different providers).

### 2.2 Gearing trigger — escalate by accumulated work (no extra call)

The loop starts **light** and escalates to **heavy** once the turn proves multi-step, via `_select_gear(substantive_tool_calls, skill_active)`:

- **heavy** when `substantive_tool_calls >= escalate_after_tool_calls` (default **2**), or
- **heavy** when a skill (a multi-step SOP) is active and `escalate_on_skill` (default true).

A *substantive* tool call excludes egress (`reply`/`respond`) and indirection meta-tools (`find_tool`/`load_tool`/`find_skill`/`use_skill`). Escalation is **sticky** for the turn (state only accumulates). Net effect: reply-only and single-tool→reply stay light; multi-tool/skilled research escalates after it is clearly deliberative. The **partial-compose finalize** runs on the light gear (wrap-up is single-dimensional).

### 2.3 Observability

The per-turn `executive_activation` event records `gearing`, `ticks_light`, `ticks_heavy`, `escalated`; the existing per-`model_call` event already records the model id, so the switch is visible in logs.

## 3. Consequences

- Single-model agents are unaffected (light_model empty → heavy everywhere).
- Cheap turns run on the cheap model; reasoning is reserved for genuine multi-step work — lower latency/cost and fewer thinking-model truncation failures on simple turns.
- The two gears may span providers (e.g. light gpt-4o-mini on OpenAI, heavy kimi on Ollama).

## 4. Alternatives considered

- **Upfront router classification** (one cheap call per turn picks the model) — cleaner per-turn separation but adds a call to every turn; rejected in favour of zero-overhead escalation.
- **Heuristic-only gate** (utterance length / active flow) — cheapest but imprecise; the escalation signal (actual tool usage) is a more reliable proxy for "multi-step".
- **Heavy plans / light executes** — inverts cost (planning is the expensive part); rejected.

## 5. Follow-up

Vision is a separate specialized action (a `VisionAction` skill + image-interpretation tool triggered on an image in `visitor.data`), not built into the executive loop — tracked separately.
