# ADR 0025 — ReplyAction is jvagent's single output contract (retire PersonaAction)

**Status**: Accepted
**Date**: 2026-06-13
**Relation**: Completes [ADR-0014](0014-identity-on-agent-replyaction-egress.md) (which split PersonaAction's concerns but retained it for Rails) and builds on [ADR-0024](0024-single-per-turn-egress.md) (single per-turn egress).

---

## 1. Context

ADR-0014 split PersonaAction into identity (→ Agent node), egress (→ ReplyAction), and a *retained* Rails coordinator (PersonaAction). That retained coordinator was the last competing output authority — a second responder that also gathered directives/parameters — and the seam behind the duplicate-egress class fixed in ADR-0024. Two output idioms coexisted: the model-authored reply (orchestrator) and directive-publishing rendered by a responder (Rails). PersonaAction (~1.5k lines) was the heavier of the two responders and the only blocker to a single egress contract.

## 2. Decision

**Retire PersonaAction. ReplyAction is jvagent's single output contract** — the one entity that gathers all queued response directives + parameters (across IAs, skills, and the orchestrator), sources conversation history, and delivers one unified reply in the Agent identity. Forward-only: no PersonaAction compatibility is retained.

- **`interaction.directives` is the one output queue.** Producers queue; ReplyAction gathers. The **orchestrator** is the author for model-authored/skill turns: the egress reply/respond tools are orchestrator-owned and route through `_send_reply`, which queues the model's reply as an `interaction.directive` (attributed to `OrchestratorInteractAction`), then calls `ReplyAction.gather(visitor)`. So `interaction.directives` reflects the turn's authored output even for interview/skill turns (whose `response_directive` is model-facing guidance, not a directive). ReplyAction **never** adds directives — `respond()`'s relayed text is a transient compose input, never persisted.
- **`ReplyAction.gather(visitor)`** is the conduit: a single relay directive (`Tell the user: …`) with no other shaping is slim-published literally (the N=1 fast path, no model call); multiple directives or any parameters/format compose into one identity-shaped reply.
- `Action.get_responder()` returns the enabled ReplyAction; **no PersonaAction fallback** (returns None if absent — agents must enable ReplyAction).
- `InteractAction.respond()` routes to ReplyAction only; ReplyAction already gathers directives/params and sources history (`_conversation_history` falls back to `interaction.conversation`).
- `publish` is the **single-directive fast path** of the same contract — a literal publish when no shaping (params/format/multiple directives) applies; not a parallel egress.
- Deliberate multi-message turns (e.g. `emit_catalog_message` + a distinct closing line) remain expressible via `_maybe_emit_final`'s exact-text guard (ADR-0024); a directive-level `standalone` flag is a future refinement.
- `jvagent/action/persona/` and its dedicated tests are deleted; identity lives on the Agent node, parameters/format/history on ReplyAction.

## 3. Consequences

- One responder, one output contract: queue a directive → ReplyAction gathers → one emission/turn (ADR-0024 latch enforces "one"). Uniform to learn, test, reason about.
- Rails agents use ReplyAction as their responder; the `minimal` scaffold profile now enables `jvagent/reply`, not `jvagent/persona`.
- `get_responder()` returning None for a ReplyAction-less agent is intentional — surfaced by scaffold defaults; a future validate-time check should warn.
- A grep-guard test (`tests/action/test_no_persona_imports.py`) proves no source references `jvagent.action.persona`.
- Migration was forward-only and green: the keystone (`get_responder` + `respond` collapse) made ReplyAction sufficient before any deletion; the full action + core + scaffold suite passes (excluding the unrelated `web_fetch` dep gap and a pre-existing `sop_extend` cache flake).
