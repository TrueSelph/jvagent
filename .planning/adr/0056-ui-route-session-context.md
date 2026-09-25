# ADR 0056 — Host SESSION CONTEXT extras via visitor.data

**Status**: Accepted
**Date**: 2026-09-24
**Relation**: Extends [ADR-0042](0042-session-context-ground-truth.md) and
[ADR-0049](0049-session-context-placement-and-cache-telemetry.md). Aligns with
[thin-harness](../../docs/thin-harness.md) invariant 3 and invariant 8
(foundation stays domain-agnostic).

---

## 1. Context

Hosts often need turn-stable **environment** facts beyond clock and channel
(e.g. where the user is in a product UI). Those facts already travel on
``visitor.data``, which is intentionally flexible — but **nothing in the
Orchestrator dumps that dict into the prompt**. Hosts were forced either to
prepend prose onto the user utterance (wrong channel; topic pollution) or to
ask the framework to parse a host-specific schema.

## 2. Decision

``render_session_context`` appends an optional host block when
``visitor.data["session_context_extra"]`` is a non-empty string (or a list of
strings joined with newlines). The harness:

- Trims and length-caps the text
- Does **not** inspect keys, ids, or product nouns inside it
- Places it inside SESSION CONTEXT (same authority class as clock/channel)

Hosts own formatting and policy wording (e.g. “optional focus — not default
answer scope”). Rich snapshots for tools stay on host-chosen keys
(``page_context``, etc.) and are **not** read by this path.

## 3. Consequences

- Framework stays schema-free; Integral (or any embedder) maps domain → prose
- Utterance stays clean; route awareness can live in the system prompt
- A buggy host can still inject up to the cap — treat as trusted host process
  (same trust as other ``visitor.data`` fields)

## 4. Alternatives considered

- Parse Integral ``page_context`` inside jvagent — rejected (domain bleed;
  collides with messenger ``page_context`` shape)
- Tool-only awareness — rejected for always-on deixis cost
- Host InteractAction only — workable with zero core change, but SESSION
  CONTEXT placement (ADR-0049) is the right channel for env facts; a one-key
  append is thinner than another InteractAction for every host
- Auto-dump entire ``visitor.data`` — rejected (PII / bloat / not facts)
