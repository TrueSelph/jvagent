# ADR 0035 — Embeddable popup chat widget (`jvmessenger`)

**Status:** Accepted
**Date:** 2026-07-23
**Implementation:** [`jvmessenger/`](../../jvmessenger), [`jvagent/messenger/server.py`](../../jvagent/messenger/server.py), [`jvagent/cli/widget.py`](../../jvagent/cli/widget.py), [`scripts/build_jvmessenger.py`](../../scripts/build_jvmessenger.py), [`jvagent/action/interact/voice_endpoints.py`](../../jvagent/action/interact/voice_endpoints.py), [`jvagent/action/interact/upload_endpoints.py`](../../jvagent/action/interact/upload_endpoints.py), [`jvagent/action/interact/public_gate.py`](../../jvagent/action/interact/public_gate.py)
**Relation:** extends [ADR-0020](0020-public-interact-endpoint-auth.md) (public interact auth), [ADR-0032](0032-interact-session-token-refresh.md) (session-token refresh). Sibling to `jvchat` ([`docs/jvchat.md`](../../docs/jvchat.md)).

## 1. Context

jvagent needed a customer-facing, embeddable popup chat — the "bubble in the
corner" a third-party site drops in with one `<script>` tag. The only existing
UI, `jvchat`, is an internal/admin SPA: it sends `X-Frame-Options: DENY` (not
embeddable), exposes reasoning/tool internals, and is not tuned for end
customers. We wanted a self-contained widget that reuses jvagent's existing
anonymous interact transport, voice actions, and file storage — adding only a
thin public surface.

## 2. Decisions

1. **Loader → iframe embed.** A framework-free `loader.js` runs in the host DOM,
   injects a Shadow-DOM launcher, and lazily creates a sandboxed iframe hosting a
   React app. Strong CSS/JS isolation; config crosses via an origin-checked
   `postMessage` handshake, never the URL.
2. **jvagent serves the bundle** via a new `jvagent/messenger/` static subpackage
   mirroring `jvagent/webui/` (single-wheel parity), **not** a CDN.
3. **Framing divergence from `jvchat`.** The widget server omits
   `X-Frame-Options: DENY` and sends a configurable
   `Content-Security-Policy: frame-ancestors` allowlist so customer sites may
   frame it. Hashed assets keep `DENY`.
4. **Self-contained frontend.** `jvmessenger/` is a standalone Vite project built
   directly on `@assistant-ui/react` with **no dependency on `jvchat`** (jvchat
   is reference only). Reasoning/tool rows (`category:"thought"`) are masked by
   default, revealed via `data-show-reasoning`.
5. **Thin public backend surface, co-located in the interact package.** Three new
   agent-scoped `auth=False` endpoints — `voice/stt`, `voice/tts`, `uploads` —
   reuse the agent's existing `BaseSTTAction`/`BaseTTSAction` and the App file
   storage. The admin `/actions/{id}/tts` routes are unusable anonymously.
6. **Uploads via a thin multipart endpoint** returning a stored URL fed into the
   next interact `data`, chosen over inline base64 (which inflates ~33% and
   collides with the per-turn media caps).
7. **Voice/uploads always require a valid `X-Session-Token`**, independent of
   `JVAGENT_INTERACT_PUBLIC_AUTH` mode — tying expensive provider calls and
   storage writes to an established conversation. Consequently they are inert in
   `off` mode (no token is minted there), which is the intended fail-closed
   behavior.

## 3. Consequences

- Ships in the same wheel as jvagent (package-data + a CI build step in
  `publish-pypi.yaml`); `jvagent messenger` serves it.
- Production requires: customer origins in `JVSPATIAL_CORS_ORIGINS`,
  `--frame-ancestors` set to those origins, `JVAGENT_INTERACT_PUBLIC_AUTH=required`
  + `JVSPATIAL_JWT_SECRET_KEY`, and `JVAGENT_PUBLIC_BASE_URL` so uploads resolve
  to fetchable URLs.
- The masking gate is pure client config over fields already on the wire — no
  backend change.
- Future: auto-TTS via a web `ChannelFilter` (mirroring the WhatsApp voice
  filter), richer markdown, and upload retention/quota policy remain open.
