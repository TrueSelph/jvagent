# ADR-0036 — Messenger engagement surface: proactive channel, page context, agent-driven UI

- **Status:** Accepted
- **Date:** 2026-07-25
- **Extends:** [ADR-0035](0035-embeddable-chat-messenger.md) (embeddable chat messenger),
  [ADR-0020](0020-public-interact-endpoint-auth.md) / [ADR-0032](0032-interact-session-token-refresh.md)
  (anonymous session tokens)
- **Constrained by:** [ADR-0024](0024-single-per-turn-egress.md) (single per-turn egress),
  [`docs/thin-harness.md`](../../docs/thin-harness.md)

## Context

ADR-0035 shipped a working but **reactive** messenger: it speaks only when spoken
to, renders only markdown text, and discards most of what the server already puts
on the wire. Meanwhile the platform underneath is far more capable than the
widget exposes — proactive dispatch, a task graph, per-user memory, a tool
surface.

An audit of the client against the server found the gap was mostly *unused
capability*, not missing capability:

- `segment_id` is minted by the server to separate distinct replies in one turn;
  the client concatenated them.
- `thought_type` (`reasoning` / `tool_call` / `tool_result` / `status`) was
  flattened into one opaque string, so tool progress could not be shown.
- The Stop button could not stop (no `AbortController`), and Regenerate was inert.
- `bridge.notify` → `launcher.setUnread` was wired end-to-end with **no caller**.
- The loader runs in the host page with full access to page and behaviour signals
  and captured **none** of them.
- A long-lived push endpoint (`/agents/{id}/reply/subscribe?stream=true`) already
  existed and already accepted the Mode B session token; nothing subscribed.

## Decisions

1. **Reuse the existing subscription for proactive delivery — no new endpoint.**
   The messenger subscribes to `reply/subscribe` with its existing
   `X-Session-Token`. Two server fixes were required and are part of this
   decision: an idle **keepalive** (proxies drop silent streams at 60-100s) and a
   **bounded backlog replay** (streaming subscribers never drain the session
   queue, so reconnects re-sent everything).

2. **Proactive is opt-in (`data-proactive`), default off.** It creates the iframe
   hidden at boot — a bundle load on every page view — and, more importantly,
   **delivery is only correct on a single worker**: `ResponseBus` is
   process-scoped, so a send in worker A is invisible to a subscriber on worker B
   and is silently dropped. *Sticky sessions do not fix this* — the publisher is a
   background scheduler, not the client. Accepted as a documented limitation; the
   durable fix is a DB-backed catch-up poll (the proactive `Interaction` *is*
   persisted — only the push is lost).

3. **The client dedups on server message id, persisted.** Both the subscribe
   backlog and the interact stream deliver the same messages, so id-dedup is
   mandatory, and the channel is suspended during a turn to avoid a race that
   would render a stray bubble.

4. **Page context is captured by the loader and travels on `data.page_context`.**
   Query strings and hashes are dropped (they routinely carry PII) and the
   referrer is reduced to its origin.

5. **Page context reaches the model via an orchestration-scoped parameter.**
   `visitor.data` is never surfaced to the model on its own, so
   `jvagent/page_context` renders it as one factual line. It must be
   **orchestration**-scoped: response-scoped parameters shape the *responder*, and
   the Orchestrator's literal `reply` path can skip that compose entirely, so the
   model would never see them while reasoning. The action states facts only —
   suggesting a next step would be turn-prep steering (thin-harness invariant 3).

6. **Agent-driven UI is *static* generative UI.** The frontend owns a fixed
   catalog (`card`, `choices`); the agent names a component and supplies data via
   a single namespaced `metadata.ui` envelope `{v, component, id, props,
   fallback}`. Rejected: declarative UI specs and open-ended HTML — both let a
   model produce off-brand or unsafe layout. A single reserved key (not sibling
   top-level keys) because `visitor.data` is merged over action metadata
   server-side; one name is one name to defend.

7. **`fallback` is required in practice.** It renders on version skew, unknown
   component, or malformed payload, and is what appears in the transcript.
   Malformed entries are dropped, never thrown — a bad payload must not break a
   turn. Link `href`s are allowlisted to `https`/`mailto`/`tel`.

8. **Component emission must follow the suggestions pattern.** An empty
   `category:"user"` message carrying only metadata does **not** trip ADR-0024's
   latch: `mark_emitted` requires non-empty content. Emission must also be
   *deferred* until after the Orchestrator, or the component renders **before**
   the reply text.

## Consequences

- Proactive-capable deployments must run `--workers 1` until a shared bus or
  catch-up poll exists.
- `data-proactive` costs a bundle load per page view when enabled.
- Rendering decisions must originate from a model tool call or a skill SOP.
  Forbidden: any server rule inspecting text to choose a component, or the
  orchestrator auto-emitting one from a tool result (thin-harness invariants
  2, 5, 6).
- New options are additive and default to today's behaviour, except `sound`
  (default on) which shipped undocumented in ADR-0035's wake and is now recorded.

## Open / not built

- **Server-side `ui__render` emitter.** Client rendering ships **inert** — nothing
  populates `metadata.ui` today. Design is settled (stage on the interaction via a
  `@tool`, flush at weight 90); implementation is outstanding.
- **Trust surface**: citations, per-message feedback, in-widget handoff state.
- **Identity/continuity**: host `identify()`, and the localStorage key collision —
  all keys are `agentId`-scoped only, so two identities on one agent collide.
- **`.planning/PROJECT.md:47` still says "Not a chat UI"**, which predates
  ADR-0035. jvmessenger is a customer-facing production UI shipped in the wheel;
  that non-goal needs restating, not silently ignoring.
