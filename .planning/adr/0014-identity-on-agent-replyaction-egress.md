# ADR 0014 — Identity on the Agent node + ReplyAction egress

**Status**: Accepted
**Date**: 2026-05-29
**Relation**: Refines [ADR-0012](0012-skill-executive-architecture.md) (SkillExecutive). `PersonaAction` (ADR-0010 era) is retained for the Rails pattern; this ADR introduces a SkillExecutive-native egress with a fallback to `PersonaAction`.

---

## 1. Context

`PersonaAction` conflates three concerns:

1. **Identity** — the agent's name, role, capabilities, voice examples.
2. **Egress / voicing** — `reply` (thin publish) and `respond` (styled generation), plus the response-bus plumbing.
3. **Rails coordination** — collecting an interaction's `directives` and `parameters` to shape the reply.

Under the SkillExecutive (ADR-0012) the orchestrator is already the coordinator — its think-act-observe loop decides what to say. A voice that *also* gathers directives/parameters to drive the reply is a second, competing coordinator (the dual-routing problem eliminated everywhere else). `PersonaAction` is also heavyweight (~1.5k lines; a large compose prompt per `respond`), and it is the *only* home for the agent's identity, which belongs at the core of the agent rather than in an egress action.

Goals: express identity centrally; give the SkillExecutive a voice that is a *pure voice* (applies shaping only when asked); and make egress congruent so every action resolves to one authority.

## 2. Decision

Split `PersonaAction`'s three concerns along two axes — identity to the Agent, egress to a new `ReplyAction` — keeping `PersonaAction` intact for Rails.

### 2.1 Identity → the Agent node

- `Agent.alias` (existing) is the display name.
- Add `Agent.role` — the agent's role/purpose, injected into prompts.
- The SkillExecutive injects an `IDENTITY` block (e.g. *"You are {alias}, {role}."*) into its system prompt, so the model writes in character from the first token. `ReplyAction` reads the same fields for `respond` styling. **One source of truth; no brain↔mouth coupling; Rails agents can set it too.**

### 2.2 Egress → ReplyAction

- New `jvagent/reply` action housing `reply` (thin literal publish), `respond` (voice provided text using Agent identity + *optional* format/params/directives), and `publish`.
- Exposes `get_tools()` → `reply`/`respond` (the same contract `PersonaAction` furnishes), so it drops directly into the SkillExecutive tool surface.
- **Lean by default**: `respond` applies identity + the keeper guardrails (no-closer, channel formatting, limits) in a single model call; the heavy compose (directives + parameters + history) runs only when those inputs are actually passed. Shaping is optional — the voice is never a coordinator.
- **`reply` is the SkillExecutive send path.** It is slim (a thin literal publish, no model call) when there is no shaping to apply, and composes via `respond` when there is — the interaction's pending **directives** (mandatory instructions), **parameters** (conditional rules), and channel **formatting**. Channel formats are housed in ReplyAction (`CHANNEL_FORMATS`, overridable per channel via the `channel_formats` descriptor attribute); the default channel carries none, so ordinary web turns stay slim for token efficiency. ReplyAction still never *collects* directives/parameters to drive a reply on its own — it only applies what the interaction (or channel) already carries.

### 2.3 Resolution → `get_responder()` with fallback

- `Action.get_responder()` returns the enabled `ReplyAction`, else `PersonaAction`.
- The SkillExecutive's persona-tool assembly and `_finalize_directives` resolve via `get_responder()`. The directive bridge inverts: the orchestrator hands directive text to `respond(text=…)` rather than the voice collecting directives.
- `PersonaAction` is unchanged and remains the egress for Rails agents (no `ReplyAction` installed).

## 3. Invariants

1. **Identity lives on the Agent** (`alias` + `role`); both the SkillExecutive prompt and `ReplyAction` read it — no duplication.
2. **The egress is a voice, not a coordinator.** `ReplyAction` applies directives/parameters/format only when a caller passes them; it never gathers them to drive a reply on its own.
3. **Egress resolution is fallback-safe.** `get_responder()` prefers `ReplyAction`, falls back to `PersonaAction`, so Rails agents keep working untouched.
4. **SkillExecutive identity is in the core prompt**, so model output is in character and `reply` (thin publish) is the common path; `respond` is for voicing text the loop did not author (e.g. rails directives).

## 4. Consequences

**Gained**: central identity at the core of the agent; a lean, SkillExecutive-native voice; congruent egress via one resolver; `PersonaAction`'s dual-coordination removed from the SE path.

**Cost**: two egress actions coexist during migration; `ReplyAction` must port the keeper voicing-quality features (channel format, response limits, guardrails).

**Deferred**: retiring `PersonaAction` and migrating Rails callers to `get_responder()` universally; multimodal (vision/voice) on `ReplyAction` until a SkillExecutive agent needs it.

**Implementation** (phased): (1) `Agent.role` + SE `IDENTITY` prompt; (2) `ReplyAction` skeleton; (3) `get_responder()` + SE wiring; (4) example/scaffold; (5) tests + docs. `PersonaAction` untouched throughout.
