# ADR 0032 — Refreshing interact session capability tokens

**Status**: Accepted
**Date**: 2026-07-16
**Implementation**: [`jvagent/action/interact/session_token.py`](../../jvagent/action/interact/session_token.py) (`refresh_grace_seconds`, `verify_session_token_for_refresh`, `refresh_session_token`), [`jvagent/action/interact/endpoints.py`](../../jvagent/action/interact/endpoints.py) (`interact_session_refresh_endpoint`). Config: `JVAGENT_INTERACT_TOKEN_REFRESH_GRACE_SECONDS`. Client: `jvchat/src/config/api.ts` (`refreshInteractSession`, session-token storage + `X-Session-Token` resend). Tests: `tests/action/interact/test_session_refresh.py`.
**Relation**: Extends [ADR-0020](0020-public-interact-endpoint-auth.md) (session capability tokens for the public interact endpoint). Does not change the interact identity guard or the channel adapters.

---

## 1. Context

ADR-0020 Mode B mints a session capability token (HS256 JWT, TTL
`JVAGENT_INTERACT_TOKEN_TTL_SECONDS`, default 7 days) that a client must
resend as `X-Session-Token` to resume its conversation. The token is
re-minted on every interact turn, so an *active* chat never expires — but:

1. **Idle expiry impairs chat.** A widget left open, or a returning visitor
   whose stored token has expired, gets `401 token_expired` on resume. There
   is no way to renew the credential without sending an utterance, and no way
   at all once `exp` has passed — the conversation (and its history) is
   stranded even though the client still holds a signature-valid capability
   bound to it.
2. **jvchat never adopted the Mode B contract.** It stripped auth from
   interact calls entirely and only attached a Mode A bearer when its login
   JWT happened to be unexpired — so in `required` mode a jvchat session with
   an expired login token could not resume either.

## 2. Decision

### 2.1 Refresh endpoint (server)

`POST /agents/{agent_id}/interact/session/refresh` (`auth=False`, rate-limited
by the interact limiter). The client presents its current token
(`X-Session-Token` header, or a `session_token` body field) and receives a
fresh one:

- **Signature, type, and agent binding** are verified exactly as on resume.
- **Expiry is relaxed to a grace window**: the token may be up to
  `JVAGENT_INTERACT_TOKEN_REFRESH_GRACE_SECONDS` (default 7 days) past `exp`.
  Beyond that, refresh is denied (`expired_beyond_grace`). `0` disables the
  window, requiring a still-valid token.
- **Conversation binding is re-checked** (`claims_match_conversation`): the
  conversation must still exist, be web-owned, and its `token_secret` must
  match the token's `cs` claim. Rotating `Conversation.token_secret`
  therefore revokes refresh exactly like it revokes resume — the grace window
  does not weaken revocation.
- On success the response carries `{session_id, user_id, session_token,
  expires_in}`; the new token has a fresh TTL, `iat`, and `jti`, and the same
  `session_id`/`user_id`/`cs` claims.
- In `off` mode the endpoint returns a validation error (tokens are not in
  use); it works in both `log` and `required` modes.

Expired tokens remain **unacceptable on `interact` itself** — the grace
window exists only at the refresh exchange, which re-binds against the live
conversation before issuing anything.

### 2.2 Client contract (jvchat as reference)

- Store the minted `session_token` per `session_id` (localStorage map, capped)
  when it arrives in the non-streaming response or the SSE `start` chunk.
- Resend it as `X-Session-Token` on every interact call (alongside the Mode A
  bearer when logged in; the server prefers the bearer).
- Before each interact call, if the stored token is expired or expiring,
  proactively call the refresh endpoint (best-effort; on `401/403` the stored
  token is dropped so the next message starts a clean session).
- Public API: `apiClient.refreshInteractSession(agentId, sessionId)`.

## 3. Consequences

**Positive**

- Long-idle conversations recover instead of stranding; active ones are
  unaffected (they already re-mint per turn).
- Clients can renew without burning an LLM turn (no utterance required).
- Revocation semantics unchanged: `rotate_token_secret()` kills both resume
  and refresh; the shared HS256 secret and claims shape are reused unchanged.
- jvchat finally implements the Mode B contract, making `required` mode safe
  to enable for jvchat deployments.

**Negative / risks**

- A leaked token's useful life extends from `TTL` to `TTL + grace` (refresh
  chains can extend it indefinitely while unrevoked) — inherent to sliding
  bearer capabilities; bounded by per-conversation revocation and the
  config knob (`0` restores strict-expiry behavior).
- One more public, unauthenticated route — mitigated by the shared rate
  limiter and the fact that it can only ever return a token the caller could
  already have obtained by holding a valid capability.

## 4. Alternatives considered

- **Grace window on `interact` itself** — one endpoint, but silently weakens
  the resume boundary for every call; rejected in favor of an explicit,
  auditable exchange.
- **Long/infinite TTL** — removes expiry pain but makes every leaked token a
  permanent credential unless rotated; rejected.
- **Separate refresh token (jvspatial-style access/refresh pair)** — heavier
  protocol for an anonymous widget; the capability token *is* the session, so
  a second credential adds surface without adding security (both would live
  in the same storage); rejected.
