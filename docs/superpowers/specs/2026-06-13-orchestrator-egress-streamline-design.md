# Orchestrator egress streamline — single output stream — design

**Date:** 2026-06-13
**Status:** Proposed (awaiting review)
**Scope:** `jvagent/action/orchestrator/` (egress in `execute()`), `jvagent/action/reply/` (ReplyAction), `jvagent/action/response/` (response bus + channel adapters), `jvagent/memory/interaction.py` (emitted latch), channel endpoints (`whatsapp`, `facebook_action`, `email_action`, `interact`).

---

## 1. Context — root cause

Duplicate responses are observed on **adapter-backed channels** (WhatsApp/Messenger/email). The webhook itself does not re-send (`finalize_whatsapp_interaction` only closes/saves —
[endpoint_helpers.py:274-339](../../../jvagent/action/whatsapp/utils/endpoint_helpers.py)),
so the duplicate is **two bus publishes within one turn**, each delivered by the channel adapter.

Three structural causes:

1. **The "already emitted" latch is `interaction.response`-emptiness**, which is not reliably set by every delivery path:
   - The **streaming** reply path returns early without setting it
     ([reply_action.py:299-300](../../../jvagent/action/reply/reply_action.py)).
   - **Directive-based publishing** (an IA queues an `interaction.directive` *and* the model also authors a reply) leaves the signal ambiguous.
   So the post-loop `_finalize_directives` ([orchestrator_interact_action.py:631](../../../jvagent/action/orchestrator/orchestrator_interact_action.py)) and `_emit_reply` fallback ([:637](../../../jvagent/action/orchestrator/orchestrator_interact_action.py)) fire a **second** publish through the same bus → adapter.

2. **Four emission points** with no single authority: loop `final` → `_maybe_emit_final`; terminal `reply`/`respond` tool; `_finalize_directives`; `_emit_reply` fallback.

3. **Two egress representations** kept loosely in sync: the **response bus** (delivery) and the persisted **`interaction.response`** — one `reply` writes both (`response_bus.publish` → `_append_to_interaction_response_impl` → `interaction.set_response`,
[response_bus.py:708](../../../jvagent/action/response/response_bus.py)). Plus **three stacked output layers** (`response_directive` → `interaction.directives` → ReplyAction) let the same content travel more than one path.

## 2. Goals / non-goals

**Goals**
- Exactly **one user-facing emission per turn per channel** (no adapter double-send).
- One **egress authority**, one **canonical output stream**, one **delivery path**, one **emitted latch**.
- `interaction.response` becomes persistence/history only — never an independent second send.

**Non-goals**
- Changing the model's reasoning loop or tool surface beyond egress.
- Removing `response_directive` from tool results (it stays the model's in-loop guidance — see §3).
- Reworking thought/trace streaming (the agent-trace channel is unaffected; this is about the `user` category).

## 3. Locked decisions

| # | Decision | Choice |
|---|---|---|
| 1 | Duplicate channel | **Adapter-backed channels** — two bus publishes per turn. |
| 2 | Canonical delivery | **The response bus is the sole delivery.** `interaction.response` is persistence/history only; endpoints/webhooks return it as data, never re-send it. |
| 3 | Output layers | **Unify on `interaction.directives`.** Subsystem/orchestrator output converges into one directive stream rendered once by ReplyAction; drop the parallel relay-then-reply duplication. |
| 4 | Scope | **Full streamline in one spec** (latch + single egress authority + unified stream + bus-only delivery + ADR). |
| 5 | `response_directive` | **Kept as the model's in-loop guidance.** Subsystems (interview/skills) are unchanged; the *emission* is unified — the model's resulting reply is the single output, not `response_directive` + reply both emitting. |
| 6 | Streaming latch trigger | **First delivered chunk.** ≥1 chunk ⇒ emitted (suppress fallback); 0 chunks ⇒ latch off (fallback recovers). |
| 7 | `interaction.response` in JSON body | **Kept** in the non-streaming `/interact` response body as the turn result — no first-party client consumes both the bus and the JSON body, so it is the single delivery for direct API callers, not a duplicate. |

## 4. Target architecture — the four "ones"

1. **One canonical stream = `interaction.directives`.** Everything the agent intends to say converges here. The model's authored reply text and any IA-queued directives compose together into one message.
2. **One egress authority = ReplyAction, once per turn.** A single egress decision emits exactly once. The four scattered emission points route through it (or gate on its latch).
3. **One delivery = the response bus.** SSE (web) / channel adapter (WhatsApp/…). `interaction.response` is set for persistence only.
4. **One emitted latch = `Interaction.emitted`.** Set by the delivery choke points (bus publish for `user` category; no-bus persist; streaming). Every re-emission path gates on it.

## 5. Detailed design

### 5.1 The emitted latch (`Interaction.emitted`)

Add a persisted boolean `Interaction.emitted` (default `False`) + helper `mark_emitted()`.

Set `True` at the **delivery choke points** for `user`-category, non-transient content:
- `ReplyAction._pipe_response` — no-bus persist branch and non-streaming bus-publish branch ([reply_action.py:287-316](../../../jvagent/action/reply/reply_action.py)).
- The **streaming** emission path — set `emitted` on the **first delivered chunk** (decision). If any token reached the user the turn counts as emitted (fallback suppressed); a stream that delivered **zero** chunks leaves the latch off so the fallback can recover. Do **not** wait for `streaming_complete` (an incomplete stream would otherwise let the fallback double-send on top of partial output) and do **not** latch at stream start (an early failure with nothing delivered would otherwise yield a silent turn).
- `response_bus.publish` for `category == "user"` and `not transient` — the single delivery choke point that already calls `_append_to_interaction_response_impl`; set `emitted` there too (on the delivered chunk / non-stream message) so adapter/SSE delivery always latches, regardless of caller.

The orchestrator's re-emission paths gate on `interaction.emitted` instead of `interaction.response`-emptiness:
- `execute()` post-loop ([:635-637](../../../jvagent/action/orchestrator/orchestrator_interact_action.py)) — fallback only if `not interaction.emitted`.
- `_finalize_directives` ([:669](../../../jvagent/action/orchestrator/orchestrator_interact_action.py)) — render only if `not interaction.emitted`.
- `_maybe_emit_final` — suppress if `interaction.emitted` (keep the exact-text echo guard as a secondary check).

This alone makes emission idempotent (Phase 0 — kills the adapter duplicate).

### 5.2 Single egress authority

Introduce one method, `_egress(visitor, *, text="", source)`, the **only** place the orchestrator initiates a user-facing emission. It:
1. returns immediately if `interaction.emitted`;
2. composes the unified output (model `text` + queued `interaction.directives`) via the responder (`ReplyAction.reply`/`respond`, channel formatting, identity, params);
3. publishes once; the choke points set `emitted`.

Refactor the four current emission points to route through `_egress`:
- `final` action → `_egress(text=answer, source="final")` (replaces `_maybe_emit_final`).
- terminal `reply`/`respond` tool → still the model calling ReplyAction directly; that path sets `emitted` via the choke point, so `_egress` post-loop no-ops.
- `_finalize_directives` → folded into `_egress` (compose queued directives when not yet emitted).
- `_emit_reply(clarify_text)` fallback → `_egress(text=clarify_text, source="fallback")`.

Net: `execute()` ends with a single `await self._egress(visitor, ...)` call; all paths converge.

### 5.3 Unified directive stream

`response_directive` (tool-result JSON) stays the model's in-loop guidance — unchanged in interview/skills. The change is at egress: when the model authors its reply, that reply is the single emission. The **parallel** path — an IA queuing the same content as an `interaction.directive` *and* the model also replying — is collapsed: `_egress` composes the model's reply **and** any unrendered `interaction.directives` into **one** ReplyAction compose, emitted once. No directive is rendered as a separate second message.

ReplyAction already composes directives + text in one `respond` call ([reply_action.py:351-352](../../../jvagent/action/reply/reply_action.py)); `_egress` always uses that single compose when directives are present, so directives never produce an independent publish.

### 5.4 `interaction.response` = persistence only

Audit every site that **delivers** `interaction.response` (vs. returns it as data):
- `interact/endpoints.py` non-streaming return ([:226](../../../jvagent/action/interact/endpoints.py)) — **keep** returning it as the JSON `response` field (decision: no first-party client consumes both the bus and the JSON body for a turn, so this is the *single* delivery for a direct non-streaming API caller, not a duplicate). Streaming clients use SSE only; non-streaming/API callers use the JSON body only.
- Channel webhooks (`whatsapp`, `facebook_action`, `email_action`) — confirm none re-send `interaction.response` after the walk (WhatsApp already does not). Any that do switch to bus-only delivery.
- Document the contract: **channel delivery is the bus; `interaction.response` is read-only history (also returned verbatim in the non-streaming JSON body for direct API callers).**

### 5.5 Egress contract (new ADR)

Write ADR-00XX refining ADR-0013/0014: "Single per-turn egress — one authority, one stream, one delivery, one latch." States: every channel receives exactly one `user` message per turn via the bus; `interaction.response` is persistence; the emitted latch is authoritative.

## 6. Data flow (turn egress, after remediation)

```
loop authors reply / queues directives
        │
        ▼
execute(): await _egress(visitor, text=final_answer)
        │  (no-op if interaction.emitted already True — e.g. terminal reply tool streamed)
        ▼
ReplyAction.respond/reply  ── composes text + interaction.directives ONCE
        │
        ▼
response_bus.publish (user)  ── SSE / channel adapter   ← single delivery
        │  └─ sets interaction.response (persistence)  + interaction.emitted = True
        ▼
channel shows ONE message; no fallback/finalize re-emits (latch set)
```

## 7. Files touched

- `jvagent/memory/interaction.py` — `emitted` attribute + `mark_emitted()`.
- `jvagent/action/reply/reply_action.py` — set latch in `_pipe_response` (both branches) and the streaming path.
- `jvagent/action/response/response_bus.py` — set latch on `user`-category non-transient `publish`.
- `jvagent/action/orchestrator/orchestrator_interact_action.py` — `_egress` authority; gate re-emission on `emitted`; collapse `final`/`_finalize_directives`/`_emit_reply`/`_maybe_emit_final`.
- `jvagent/action/whatsapp/`, `facebook_action/`, `email_action/`, `interact/endpoints.py` — audit; ensure bus-only delivery; return `interaction.response` as data only.
- `.planning/adr/00XX-single-per-turn-egress.md` — new ADR.

## 8. Testing

- **Emitted-latch idempotency:** a turn that emits via streaming → `interaction.emitted` True → `_finalize_directives`/fallback no-op (assert single publish).
- **Adapter no-double-send:** a fake ChannelAdapter records sends; assert exactly one `user` send per turn for: plain reply, directive-publishing IA, streaming reply, interview turn.
- **Directive + reply compose once:** an IA queues a directive and the model replies → one composed message, not two.
- **`interaction.response` not re-sent:** webhook/endpoint path delivers via bus only; `interaction.response` present as data, no second adapter send.
- **Regression:** existing orchestrator egress tests (`test_*egress*`, `test_*reply*`, `test_flow_*`) green.

## 9. Risks / guardrails

- **Streaming latch timing (resolved):** latch on the **first delivered chunk**. A stream that delivered ≥1 chunk suppresses the fallback (never double); a stream that delivered zero chunks leaves the latch off so the fallback recovers (never silent). Not stream-start, not stream-complete.
- **Non-channel callers** (the JSON `/interact` API) still need `interaction.response` in the body — keep returning it as data; only stop treating it as a *send*.
- **`_maybe_emit_final` exact-text guard** must remain for the product-skill "distinct closing line" case (a non-terminal publish mid-turn followed by a distinct final answer).
- **Transient/thought content** must not set the `user` latch (only `category == "user"`, `not transient`).

## 10. Acceptance criteria

1. Each turn delivers exactly one `user` message per channel; adapter records a single send for plain, streaming, directive, and interview turns.
2. `interaction.emitted` is the authoritative latch; `_finalize_directives`/fallback/`final` never produce a second emission once set.
3. `response_directive` and interview/skill behavior unchanged; the model's reply is the single emission.
4. `interaction.response` is delivered by no path other than the bus; endpoints return it as data only.
5. New ADR recorded; egress-contract tests green; full `tests/action/orchestrator/` + `tests/action/` (excluding unrelated `web_fetch`) green.
