# ADR 0056 — UI ROUTE in SESSION CONTEXT

**Status**: Accepted
**Date**: 2026-09-24
**Relation**: Extends [ADR-0042](0042-session-context-ground-truth.md) (SESSION
CONTEXT ground truth) and [ADR-0049](0049-session-context-placement-and-cache-telemetry.md)
(placement). Aligns with [thin-harness](../../docs/thin-harness.md) invariant 3
(environment facts ≠ prep steering).

---

## 1. Context

Hosts such as Integral already place a UI snapshot on
``visitor.data["page_context"]`` (path, page kind, breadcrumbs, focused
resource ids, display titles). Integral previously **prepended** a delimited
prose stub onto the **user utterance** so the model could see the route. That
channel treats on-screen Apps as *topic*, bloats the transcript, and invites
overfitting (e.g. answering personal-expense questions from a Sales board).

SESSION CONTEXT already carries turn-stable environment facts (clock, channel).
UI location is the same class of fact.

## 2. Decision

When ``visitor.data`` contains a usable ``page_context`` dict,
``render_session_context`` appends a compact **UI ROUTE** block inside
SESSION CONTEXT:

- Dense lines: ``kind``, human labels + ids for focused app/track/view/entry
  (and optional ``dashboard_id`` from metadata), ``path``, ``crumbs``
- Fixed authority line: focused ids are **optional** — apply only for
  this/here/crumb match or a clear resource match; do not answer from the
  focused resource merely because it is on screen
- **No** visible entry/track lists (hosts expose those via a page-context tool)
- **No** tool names or next-step cues (thin-harness)
- Messenger-style ``title`` + ``path`` remains a thin fallback

Hosts that previously injected utterance preambles should stop; the snapshot
on ``visitor.data`` is enough.

## 3. Consequences

- Route awareness survives without polluting the user message
- Always-on cost stays small (~3–5 lines) — no soft/minimal utterance gate
- Integral (and similar hosts) keep a tool for on-screen lists / full snapshot
- Prompt-cache prefix above SESSION CONTEXT unchanged (ADR-0049)

## 4. Alternatives considered

- Utterance prepend with HTML delimiters — rejected (topic pollution)
- Tool-only awareness — rejected (extra tick for every deixis)
- Reuse messenger ``PageContextInteractAction`` — rejected (wrong schema;
  response parameters, not system ground truth)
- Soft/minimal utterance gate — transitional only; superseded by this ADR
