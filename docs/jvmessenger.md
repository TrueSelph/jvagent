# jvmessenger — embeddable popup chat

`jvmessenger` is a customer-facing chat messenger any third-party site embeds with a
single `<script>` tag. It is a self-contained React app built on the
[assistant-ui](https://github.com/assistant-ui/assistant-ui) base components,
talking to a jvagent server over the public interact API. It is distinct from
[`jvchat`](jvchat.md), the internal/admin SPA.

- **Source:** [`jvmessenger/`](../jvmessenger) (standalone Vite project; no dependency
  on `jvchat`).
- **Served by:** `jvagent messenger` — a stdlib static server
  ([`jvagent/messenger/server.py`](../jvagent/messenger/server.py)) that ships the
  built bundle as wheel package-data (built by
  [`scripts/build_jvmessenger.py`](../scripts/build_jvmessenger.py)).
- **Design record:** [ADR-0035](../.planning/adr/0035-embeddable-chat-messenger.md).

## Embedding

The agent can be bound either via `data-agent-*` attributes **or** via query
params on the loader `src` (URL-param binding), so a copy-paste snippet with the
ids baked into the URL works with no attributes:

```html
<script
  src="https://agent.host/messenger/loader.js?agentId=n.Agent.123&agentUrl=https://agent.host"
  data-title="Support"
  data-greeting="Hi! How can I help?"
  data-quick-replies='["Track my order","Talk to a human"]'
  data-avatar="https://acme.com/bot.png"
  data-description="Your friendly support assistant"
  data-theme="auto"
  data-show-reasoning="false"
  data-attachments="true"
  data-voice="true"
  data-fullscreen="true"
></script>
```

`loader.js` (a tiny framework-free IIFE, ~7 kB) reads its own `data-*` attributes
and loader-URL query params, injects a Shadow-DOM launcher button into the host
page, and — on first open — creates a sandboxed `<iframe>` hosting the chat app.
The resolved config reaches the iframe over an **origin-checked `postMessage`
handshake**, never via the iframe URL. Query params override `data-*` when both
are present.

### Config options (`data-*` or loader-URL query param)

Each option can be set as a `data-foo-bar` attribute or a `?fooBar=` query param
on the loader src.

| Option | Default | Meaning |
|---|---|---|
| `agent-url` | — (**required**) | Agent server base URL the app calls. |
| `agent-id` | — (**required**) | Target agent id (interact path param). |
| `title` | agent name¹ | Header title / agent name. |
| `description` | agent description¹ | Short line shown under the name in the header. |
| `greeting` | — | Opening assistant message (welcome screen). |
| `quick-replies` | — | JSON array (or CSV) of quick-reply cards on the welcome screen. |
| `notice` | — | Info banner pinned above the thread (e.g. "responses may be slower"). |
| `consent` | — | Data-use disclosure shown with Accept/Decline before the first message. Acceptance is remembered per agent (re-worded text re-prompts). |
| `avatar` | agent avatar¹ → default | Agent avatar image URL. |
| `theme` | `auto` | `light` / `dark` / `auto` (follows the system setting, live). |
| `show-reasoning` | `false` | Reveal reasoning/tool rows (masked by default). |
| `attachments` | `false` | Enable file uploads. |
| `voice` | `false` | Enable mic (STT — real-time when supported, batch fallback) + read-aloud (TTS). |
| `fullscreen` | `true` | Allow expanding to a centered fullscreen view. |

¹ When `avatar` / `title` / `description` are not set, the messenger fetches the
agent's public profile (`GET /agents/{id}/profile`) and uses its avatar (from a
loaded `AvatarAction`), name, and description; the avatar falls back to a
built-in default glyph. Precedence per field: `data-*`/query → agent profile →
default.

### Behavior notes

- **Single conversation thread**, persisted client-side (localStorage, keyed to
  the session) so a page refresh keeps the history. The server session
  (`session_id` + capability token) persists alongside it.
- **Theme** follows the OS `prefers-color-scheme` live (updates without reload)
  unless `theme` pins `light`/`dark`.
- **Fullscreen** expands to a centered, max-width card over a blurred, dimmed
  backdrop of the host page.
- **Masking:** reasoning/tool rows (`category:"thought"`) are hidden by default,
  shown only when `show-reasoning` is on.

### Agent-driven suggestions (follow-up chips)

The agent can offer clickable follow-up chips after a reply by putting them on
the outbound message's `metadata` (`ResponseMessage.metadata`). The messenger
renders them above the composer; clicking one sends it as the next turn. Two
shapes are supported and merged:

```jsonc
// quick replies — the label is sent verbatim as the utterance
"metadata": { "suggestions": ["Track my order", "Talk to a human"] }
// actions — a distinct value is sent (e.g. to route a skill)
"metadata": { "actions": [{ "label": "Request a refund", "value": "refund_flow" }] }
```

The messenger reads these off each turn's messages (client-side —
`extractSuggestions`), so no widget change is needed to add options; the agent
just attaches metadata to its published reply. (Emitting this from the
Orchestrator/skills is the agent-side half — a small directive/tool follow-up.)

## Serving

```bash
python scripts/build_jvmessenger.py          # build + stage the bundle (needs Node)
jvagent messenger --port 3100                 # serve loader.js + app on :3100
jvagent messenger --frame-ancestors "https://acme.com https://shop.acme.com"
```

The server serves `loader.js` (permissive CORS, uncached) and the iframe app
(`app.html` + hashed assets). Unlike `jvchat`'s server, it does **not** send
`X-Frame-Options: DENY` — embeddable pages instead carry a configurable
`Content-Security-Policy: frame-ancestors` allowlist (default `*` for dev). Set
`--frame-ancestors` to the customer origins in production.

## Backend endpoints

All are agent-scoped and `auth=False` (public). The messenger uses the existing
interact stream plus a small public surface:

| Endpoint | Auth | Purpose |
|---|---|---|
| `POST /api/agents/{id}/interact` | session-token per mode | Streaming chat (SSE). |
| `POST /api/agents/{id}/interact/session/refresh` | — | Renew the session token. |
| `GET  /api/agents/{id}/profile` | none | Agent avatar + name + description (public branding). |
| `POST /api/agents/{id}/voice/stt` | **X-Session-Token required** | Transcribe a base64 clip, batch (reuses `BaseSTTAction`). |
| `WS   /api/agents/{id}/voice/stt/stream` | **token query param required** | Real-time STT: stream mic audio → interim + final transcripts. |
| `POST /api/agents/{id}/voice/tts` | **X-Session-Token required** | Synthesize speech (reuses `BaseTTSAction`). |
| `POST /api/agents/{id}/uploads` | **X-Session-Token required** | Multipart upload → URL for the next interact `data`. |

Voice + upload **always** require a valid `X-Session-Token` (minted by a prior
interact turn) regardless of `JVAGENT_INTERACT_PUBLIC_AUTH` mode — so they are
inert in `off` mode by design (no token is minted there). `/profile` is
unauthenticated branding, served read-only. New public routes live in the
interact package ([`voice_endpoints.py`](../jvagent/action/interact/voice_endpoints.py),
[`voice_stream_endpoints.py`](../jvagent/action/interact/voice_stream_endpoints.py),
[`upload_endpoints.py`](../jvagent/action/interact/upload_endpoints.py),
[`avatar_endpoints.py`](../jvagent/action/interact/avatar_endpoints.py)) and reuse
its rate limiter + session-token gate ([`public_gate.py`](../jvagent/action/interact/public_gate.py)).

### Real-time STT (streaming)

When `voice` is enabled, the mic **prefers live transcription**: the browser
streams `MediaRecorder` webm/opus chunks over the `voice/stt/stream` WebSocket to
the agent's STT provider (Deepgram's live API via `DeepgramSTTAction.stream_transcribe`),
and interim + final transcripts fill the composer as the user speaks. If the
browser can't stream (no `MediaRecorder`, mic denied, socket refused, or the STT
provider has no `stream_transcribe`), the mic **falls back** to the batch
`POST /voice/stt` path automatically — no config needed.

Because browsers can't set custom headers on a WebSocket handshake, the session
token rides as the `?token=` query param (verified the same way as the header on
the POST routes). **Serve over `wss://` in production** so the token isn't exposed
in plaintext. The WS route is registered by wrapping the server's app factory
([`register_voice_ws_routes`](../jvagent/action/interact/voice_stream_endpoints.py)),
because jvspatial's `@endpoint` is HTTP-only and app rebuilds replay only HTTP
routes — the wrapper puts the route on every built app so it survives rebuilds.

## Production checklist

- **CORS origins:** the iframe app (served from the messenger origin) calls the
  agent cross-origin, so add the **messenger origin** to `JVSPATIAL_CORS_ORIGINS`.
  In dev, serve the messenger on an already-allowlisted origin (e.g. `:3000`) or
  add its origin to the list — otherwise the interact preflight fails with `400`.
- **CORS headers:** the client sends the `X-Session-Token` header on resume /
  voice / upload calls, so it must be in the allowed CORS headers. It is **not**
  in the default set — add it via `JVSPATIAL_CORS_HEADERS` (include the defaults
  too), e.g.
  `JVSPATIAL_CORS_HEADERS="Accept,Authorization,Content-Type,X-API-Key,X-Session-Token"`.
  Symptom when missing: the first turn works but the next turn (which carries the
  token) fails the preflight and the client shows "Could not reach the agent."
- **Framing:** set `--frame-ancestors` (or the server's `frame_ancestors`) to the
  customer origins — not `*`.
- **Session auth:** run `JVAGENT_INTERACT_PUBLIC_AUTH=required` and set
  `JVSPATIAL_JWT_SECRET_KEY`. Voice/uploads require a token, so they are inert in
  `off` mode by design.
- **Uploads reachable by the model:** set `JVAGENT_PUBLIC_BASE_URL` so uploaded
  files resolve to absolute, fetchable URLs for the vision pipeline.
- **Voice providers:** configure the agent's STT/TTS actions and their keys
  (`DEEPGRAM_API_KEY`, `ELEVENLABS_API_KEY`). Absent providers make the messenger
  hide the mic/speaker controls.
- **Avatar/branding:** set the agent's `AvatarAction` image (and its
  `name`/`description`) to have the messenger show the real agent identity with no
  `data-*` overrides.
