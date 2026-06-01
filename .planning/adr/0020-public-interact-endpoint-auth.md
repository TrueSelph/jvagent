# ADR 0020 — Authenticating the public `interact` endpoint (session capability tokens)

**Status**: Proposed (design approved; staged implementation pending)
**Date**: 2026-06-01
**Relation**: Hardens the HTTP ingress in front of the interact subsystem (`jvagent/action/interact/endpoints.py`). Builds on **jvspatial** auth facilities (`jvspatial/api/auth/service.py` `AuthService`, `api_key_service.py` `APIKeyService`, `AuthConfig`/`JVSPATIAL_JWT_SECRET_KEY`). Does **not** touch `InteractWalker` or the channel adapters.

---

## 1. Context

`POST /agents/{id}/interact` (`endpoints.py:480`) is intentionally `auth=False`: it
must serve **public, anonymous** chat sessions — embedded website pop-up widgets
that mint a `user_id`/`session_id` on first contact and then **reuse** the
`session_id` to resume the conversation.

Today both `user_id` and `session_id` are **client-asserted strings with no proof
of ownership**. Session resolution (the docstring's scenarios 1–4) means:

- `session_id` only → resumes that conversation and **returns its `user_id`**.
- `user_id`+`session_id` → resumes if the session "belongs to" that user — but
  the caller supplies both.

The only existing controls are an IP+agent rate limiter (`rate_limiter.py`) and
the utterance-length cap.

### 1.1 Threat

`session_id` is acting as a **bearer credential the server never issued and
cannot verify**. Consequences:

1. **Conversation hijack / IDOR.** Anyone who learns or guesses a `session_id`
   (Referer, logs, history, shared URLs, low entropy) can resume the
   conversation, read its history back through the response, and continue as
   that user. The "`session_id` → returns `user_id`" path is an **enumeration
   oracle**.
2. **Identity forgery.** `user_id` is unauthenticated; a visitor can claim any
   `user_id`. Scenario 2 bypasses the ownership check entirely.
3. **Cost abuse / DoS.** Every call hits an LLM, so this is a money-burning
   endpoint; IP rate limiting is weak against NAT/proxy/IPv6 rotation.
4. **Cross-ingress reach.** Channel sessions (WhatsApp `wa_<phone>`, Messenger
   PSID) live in the same store. An attacker on the **web** endpoint could try to
   resume a **channel-owned** session by supplying its id.

### 1.2 Scope boundary

`InteractWalker` is a **trusted internal primitive**; every ingress is
responsible for establishing identity *before* it spawns the walker. The channel
webhooks already do this at their own edge — WhatsApp/Messenger verify the
provider **signature** (Meta `X-Hub-Signature`) and derive `user_id` from the
provider-verified sender; email via DKIM/verified sender; `task_dispatcher` /
proactive sends are server-originated. **This ADR changes only the public web
ingress.** The walker and the channel adapters are out of scope.

## 2. Decision

The endpoint resolves identity through **one of two doors**, then threads the
*verified* `user_id`/`session_id` into `InteractWalker`. No bespoke crypto — we
reuse jvspatial's JWT signer and key service.

### 2.1 Mode A — Authenticated bearer (jvchat, embed hosts)

If a valid jvspatial JWT is present (`Authorization: Bearer`), trust
`user_id = sub` via `AuthService.validate_token`. No session token needed.
jvchat already holds a login JWT (`useAuth`) but does not yet send it to
`interact`; it will (see §3).

### 2.2 Mode B — Anonymous session capability token (public widget)

No bearer →

- **Create** (first message, no token): generate a high-entropy `session_id`
  (≥128-bit, `secrets.token_urlsafe`), create the anon `User`/`Conversation`,
  stamp a per-`Conversation` `token_secret`, and **mint a short-lived session
  JWT** — reusing `AuthService`'s HS256 signer + `JVSPATIAL_JWT_SECRET_KEY` —
  with claims `{agent_id, session_id, user_id, channel:"web", cs:<token_secret>,
  iat, exp}`. Return it (`X-Session-Token` header + body field).
- **Resume** (any call carrying `session_id`/`user_id`): the token is
  **required**. Verify signature + `exp`, then check the claims match the loaded
  `Conversation` (`session_id`, `user_id`, and `cs == Conversation.token_secret`).
  The bare "`session_id`-only resumes and returns `user_id`" **oracle is
  removed**.

**Revocation is free**: rotate `Conversation.token_secret` (we already load the
Conversation on resume) to invalidate a leaked/abused token — no token table, no
global secret roll.

### 2.3 Cross-channel scoping

Mode B tokens are `channel:"web"`-scoped. Resume **rejects** any `session_id`
whose `Conversation` belongs to a non-web channel, so the open web door cannot
touch a channel-owned session (§1.1.4).

### 2.4 Defense-in-depth (not the boundary)

- Per-agent **Origin/Referer allowlist** + CORS (spoofable off-browser, so
  defense-in-depth only).
- **Turnstile/hCaptcha on create** (cheap, one-time per visitor).
- Per-session/token **turn + cost budget** layered on the IP+agent limiter.
- A **publishable/embed key** per agent via jvspatial `APIKeyService`
  (`generate_key`/`validate_key`/`revoke_key`) identifies the tenant + binds
  allowed origins; never identifies the user.

### 2.5 Configuration + rollout (staged)

`AuthConfig`/env gate `JVAGENT_INTERACT_PUBLIC_AUTH ∈ {off, log, required}`:

1. **`log`** (first release): mint + verify, but **never reject** — log what
   *would* be denied. No breakage on deploy.
2. Migrate jvchat (Mode A) and the widget (Mode B).
3. **`required`**: enforce. Backfill `Conversation.token_secret` lazily on next
   resume for pre-existing conversations.

`off` preserves today's behavior (for embed hosts that own identity themselves).

## 3. Client contract

- **jvchat** (authenticated): send its login JWT as `Authorization: Bearer`
  (Mode A); trust `user_id = sub`. It already captures `session_id` from the
  first stream chunk (`useStreaming.ts:356`) — keep that; stop depending on the
  removed `session_id`-only oracle. Mostly header wiring in the axios client.
- **Public embeddable widget** (anonymous): store the minted `session_token`
  (cookie/localStorage), send it on every call, handle `401 → re-create`. This
  is the new documented protocol and the primary new client.
- **`jvagent/embed.py` library hosts**: unchanged; they mount their own surface
  and resolve `user_id`. Documented that the guard is config-gated (`off`).
- Audit any other `/interact` consumer (dashboards, tests) → migrate to Mode A.

## 4. Implementation surface (for the follow-up build)

1. `Conversation` node: add `token_secret` (+ `channel`/origin) attribute + a
   (re)issue helper.
2. `jvagent/action/interact/session_token.py`: `mint_session_token()` /
   `verify_session_token()` — thin wrappers over `AuthService` signing + claim
   checks.
3. `interact_endpoint`: an identity guard at the top (Mode A / Mode B /
   create-vs-resume) replacing blind trust; thread verified ids into the walker;
   add `session_token` to the create response + header; remove the user_id oracle.
4. Config: `JVAGENT_INTERACT_PUBLIC_AUTH`, token TTL, Origin allowlist,
   Turnstile keys — default `off`/`log`.
5. Per-session/token budget hooks atop the existing limiter.

## 5. Consequences

**Positive**

- `session_id` stops being a forgeable bearer; resume requires an unforgeable,
  server-signed capability. Hijack now requires forging an HS256 signature.
- The enumeration oracle is gone; identity forgery is closed (Mode A binds to a
  real JWT; Mode B binds to a server-minted token + per-conversation secret).
- Per-conversation revocation without new infra.
- Reuses jvspatial's audited auth surface — one signing mechanism, one secret.
- Reusable `session_id` + dynamic-anonymous flows are preserved.

**Negative / risks**

- New client contract for the widget (+ minor jvchat wiring); staged rollout
  mitigates lockout risk.
- A leaked **valid token** before expiry still grants access until `exp` or
  `token_secret` rotation — inherent to bearer capabilities; bounded by short
  TTL + revocation.
- Origin allowlisting is not a real auth boundary (spoofable off-browser); it is
  abuse-reduction only.
- Turnstile adds a dependency + a create-time round-trip (gate it per agent).

## 6. Alternatives considered

- **Opaque HMAC token** instead of a jvspatial JWT — smaller surface, but a
  second mechanism alongside jvspatial's JWT; rejected for consistency now that
  we build on jvspatial.
- **Purely stateless token (no per-conversation secret)** — simpler, but
  unrevocable before `exp`; rejected because the credential is also the
  abuse/cost vector and the Conversation is already loaded on resume.
- **Full login for anonymous visitors** — defeats the embeddable, frictionless
  use case; rejected.
- **Leave as-is, rely on rate limiting** — does not address hijack, forgery, or
  the oracle; rejected.

## 7. Out of scope

- `InteractWalker` and the channel adapters (own their own edges).
- Authn for already-authenticated internal surfaces.
- `requires-action-versions`-style concerns and unrelated endpoint hardening.

## 8. Testing (follow-up)

Token mint/verify (valid / expired / tampered / wrong-secret / wrong-channel);
create→resume happy path; resume-without-token rejected; Mode A bearer path;
oracle removed; cross-channel resume rejected; rate/budget; jvchat + widget
client integration; staged-mode (`log` never rejects).
