# ADR 0024 — Single per-turn egress (one authority, one stream, one delivery, one latch)

**Status**: Accepted
**Date**: 2026-06-13
**Relation**: Refines [ADR-0013](0013-togglable-deterministic-turn-lock.md) (turn-lock egress) and [ADR-0014](0014-identity-on-agent-replyaction-egress.md) (ReplyAction egress).

---

## 1. Context

Adapter-backed channels (WhatsApp/Messenger/email) showed **duplicate responses**. The webhook does not re-send (`finalize_whatsapp_interaction` only closes/saves); the duplicate was **two bus publishes within one turn**, each relayed by the channel adapter.

Root causes:
1. The "already emitted" signal was `interaction.response`-emptiness, which is **not reliably set by every delivery path** — the streaming path returned early without setting it, and directive-based publishing (an IA queues a directive *and* the model also replies) left it ambiguous. So the post-loop `_finalize_directives` and the `_emit_reply` clarify fallback fired a second publish.
2. **Four emission points** with no single authority: loop `final` (`_maybe_emit_final`), terminal `reply`/`respond`, `_finalize_directives`, the clarify fallback.
3. **Two egress representations** kept loosely in sync — the response bus (delivery) and the persisted `interaction.response` — one `reply` writes both.

## 2. Decision

A single per-turn egress contract, expressed as four "ones":

1. **One emitted latch — `Interaction.emitted`.** Set `True` at the delivery choke points for `user`-category, non-transient content: `response_bus._deliver_flush`, the first delivered SSE `stream_chunk`, and `ReplyAction._pipe_response` (no-bus branch). For streaming the latch flips on the **first delivered chunk** (≥1 chunk ⇒ emitted, suppress the fallback; 0 chunks ⇒ latch stays off so the fallback can recover — never silent, never double).
2. **One post-loop egress authority — `OrchestratorInteractAction._egress`.** It renders any queued rails-IA directives once, then falls back to `clarify_text`, all gated by the latch. `execute()` ends with a single `await self._egress(visitor)`.
3. **One delivery — the response bus.** Channels are delivered to by the bus (SSE / channel adapter) during the walk. No channel webhook re-sends `interaction.response` after the walk.
4. **`interaction.response` is persistence/history only.** It is also returned verbatim in the non-streaming `/interact` JSON body as the turn result for direct (non-channel) API callers — that is the *single* delivery for such a caller, not a duplicate (no first-party client consumes both the bus and the JSON body for one turn).

`response_directive` (the JSON field interview/skills emit) is unchanged — it remains the model's in-loop guidance; the *emission* is unified, so the model's resulting reply is the single output.

**Intentional multi-message exception:** `_maybe_emit_final` is **not** latch-gated by a blanket check. A distinct `final` answer is still allowed after a mid-turn non-terminal publish (e.g. `emit_catalog_message` followed by a product-skill closing line); it suppresses only on **exact-text** echo. The latch governs *accidental* double-emission (fallback/finalize), not deliberate multi-message turns.

## 3. Consequences

- Exactly one user message per turn per channel for ordinary turns; the adapter records a single send. Verified by `tests/action/orchestrator/test_adapter_no_double_send.py`, `test_egress_idempotent.py`, `test_single_egress.py`, and `tests/action/response/test_emitted_latch.py`.
- Egress reasoning is centralized: post-loop emission lives in `_egress`; the latch is the authoritative "did this turn deliver?" signal, replacing brittle `interaction.response`-emptiness checks.
- `interaction.response` may be read freely as history; it must never be treated as a *send* by channel code.
