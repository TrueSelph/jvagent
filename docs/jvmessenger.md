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
| `sound` | `true` | Subtle chime when an assistant message arrives (synthesized, no asset). |
| `teaser` | — | Greeting shown in the launcher teaser card, with an inline mini-composer, **before** the panel is opened. Empty disables the teaser. |
| `teaser-delay` | `4000` | Milliseconds before the `delay` trigger fires. |
| `teaser-cooldown-days` | `7` | How long a dismissal suppresses the teaser (per host origin). `0` = this page view only. |
| `teaser-triggers` | `delay` | JSON array (or CSV) of `delay` / `scroll` / `exit` / `idle`. First match wins, once per page view. |
| `teaser-scroll-percent` | `55` | Scroll depth (0-100) that fires the `scroll` trigger. |
| `page-context` | `true` | Send host-page context (path, title, referrer origin, dwell, scroll depth, repeat visit) to the agent. See [Page context](#page-context). |
| `proactive` | `false` | Subscribe to the persistent session channel so the agent can push messages between turns. See [Proactive messages](#proactive-messages) — **read the deployment caveat first**. |

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
just attaches metadata to a published message.

**Emitting them:** the core `jvagent/suggestions` action
([`suggestions_interact_action.py`](../jvagent/action/suggestions/suggestions_interact_action.py))
generates the chips with an LLM after each reply and publishes an empty
`category:"user"` message carrying `metadata.suggestions`. Add it to an agent
and point it at a light model:

```yaml
- action: jvagent/suggestions
  context:
    enabled: true
    model_action_type: OpenAILanguageModelAction
    model: gpt-4o-mini      # keep it light — this is one extra call per turn
    num_suggestions: 3
    max_words: 8            # longer suggestions are dropped, never truncated
    avoid_data_requests: true
```

It runs after the Orchestrator (weight 100), only on **streaming** turns (so it
never posts an empty message to non-streaming channels), and no-ops when no
model is available or the output can't be parsed. The prompt asks for chips
phrased as a **first-person statement or question in the user's voice** (e.g.
"How much does it cost?"), and `avoid_data_requests` drops any that ask the user
to supply personal/contact data (e.g. "Share my email") — a tapped chip sends
its label verbatim and can't carry that data. The leadgen reference agent
enables it. Any action can alternatively attach `metadata.suggestions` /
`metadata.actions` to its own published message.

## Launcher teaser

When `teaser` is set, the launcher shows a labelled pill, a dismissible greeting
card, and an **inline mini-composer** — all rendered in the Shadow-DOM launcher,
so they work before the chat iframe exists. Text typed there opens the panel and
is sent as the first turn.

Which trigger reveals it is controlled by `teaser-triggers`:

| Trigger | Fires when |
|---|---|
| `delay` | `teaser-delay` ms have passed |
| `scroll` | the visitor scrolls past `teaser-scroll-percent` |
| `exit` | the pointer leaves through the top of the viewport (exit intent; no-op on touch) |
| `idle` | as `delay`, for embeds that only want dwell-based engagement |

First match wins, once per page view. It never fires while the visitor is typing
in one of the host page's own form fields, never covers an already-open panel,
and a dismissal is remembered for `teaser-cooldown-days` on the host origin.
Entrance and attention animations are disabled under `prefers-reduced-motion`.

## Page context

With `page-context` on (the default), the loader — which runs in the host page —
reports where the visitor actually is, and the app forwards it on each turn as
`data.page_context`:

```jsonc
{ "origin": "https://acme.com", "path": "/pricing", "title": "Pricing",
  "referrer": "https://google.com",        // origin only
  "secondsOnPage": 95, "scrollDepth": 80,
  "visitCount": 3, "returning": true }
```

**Privacy:** query strings and hashes are deliberately dropped — they routinely
carry emails, tokens and order ids — and the referrer is reduced to its origin.

`visitor.data` is never surfaced to the model on its own, so the core
[`jvagent/page_context`](../jvagent/action/page_context/) action renders this as
one factual line and contributes it as an **orchestration-scoped** parameter.
That scope matters: response-scoped parameters shape the responder, and the
Orchestrator's literal `reply` path can skip that compose entirely, so the model
would never see them while reasoning. Enable it on the agent:

```yaml
- action: jvagent/page_context
  context:
    enabled: true
```

The action states facts only — it never suggests a next step, which would be
turn-prep steering (`docs/thin-harness.md` invariant 3).

## Proactive messages

With `proactive` on, the app subscribes to
`POST /agents/{id}/reply/subscribe?stream=true` (the same Mode B session token it
already holds) so the agent can speak between turns — `Agent.send_proactive_message`
or a `TaskMonitor` follow-up. Messages arriving while the panel is closed badge
the launcher.

**Off by default, for two reasons:**

1. It creates the chat iframe hidden at boot so the app can subscribe before the
   panel is ever opened — that loads the bundle on every page view.
2. **Delivery requires a single-worker deployment.** `ResponseBus` is
   process-scoped, so a proactive send in worker A is invisible to a subscriber
   on worker B and is silently dropped. **Sticky sessions do not fix this** — the
   publisher is a background scheduler, not the client. Run proactive-capable
   deployments with `--workers 1`. The durable fix (a DB-backed catch-up poll) is
   not built.

The client dedups on server message id (persisted), because the subscribe
backlog replays on every reconnect and is never drained by streaming
subscribers; it also suspends the channel during a turn, since one bus feeds both
transports.

## Agent-driven UI components

The messenger owns a fixed component catalog; the agent names a component and
supplies data on `metadata.ui`. A model can never inject markup or layout — only
fill in a shape the frontend defined.

```jsonc
"metadata": {
  "ui": {
    "v": 1,
    "component": "card",              // card | choices
    "id": "ui_ord1042",               // stable id (render dedup)
    "fallback": "Order #1042 — shipped, arrives Fri Aug 1.",
    "props": { "title": "Order #1042", "subtitle": "Shipped",
               "fields": [{ "label": "Carrier", "value": "DHL" }],
               "actions": [{ "label": "Track", "kind": "link",
                             "href": "https://…" }] }
  }
}
```

`fallback` is required in practice: it renders on version skew, an unknown
component, or a malformed payload, and it is what appears in the downloaded
transcript. Malformed entries are dropped rather than thrown. Link `href`s are
allowlisted to `https`/`mailto`/`tel`. `choices` sends the tapped label verbatim,
the same contract as the suggestion chips.

> **Status:** rendering is implemented; **no server-side emitter ships yet**, so
> nothing populates `metadata.ui` today. Any action can attach it manually.

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
| `POST /api/agents/{id}/voice/stt/stream/ticket` | **X-Session-Token required** | Mint a short-lived WebSocket ticket (`?ticket=`). |
| `WS   /api/agents/{id}/voice/stt/stream` | **ticket query param required** (legacy `token` accepted) | Real-time STT: stream mic audio → interim + final transcripts. |
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

Because browsers can't set custom headers on a WebSocket handshake, the messenger
first `POST`s `/voice/stt/stream/ticket` with `X-Session-Token` (same header gate
as the other voice routes) and opens the socket with the short-lived `?ticket=`
query param — so the long-lived session capability token never lands in access
logs or Referer chains. Older clients that still send `?token=` (the full session
token) are accepted for compatibility. **Serve over `wss://` in production** so
the credential isn't exposed in plaintext. The WS route is registered by wrapping
the server's app factory
([`register_voice_ws_routes`](../jvagent/action/interact/voice_stream_endpoints.py)),
because jvspatial's `@endpoint` is HTTP-only and app rebuilds replay only HTTP
routes — the wrapper puts the route on every built app so it survives rebuilds.

## Production checklist

- **CORS origins:** the iframe app (served from the messenger origin) calls the
  agent cross-origin, so add the **messenger origin** to `JVSPATIAL_CORS_ORIGINS`.
  In dev, serve the messenger on an already-allowlisted origin (e.g. `:3000`) or
  add its origin to the list — otherwise the interact preflight fails with `400`.
- **CORS headers:** the client sends the `X-Session-Token` header on resume /
  voice / upload calls. jvagent **auto-allowlists** that header when building the
  server CORS config (`_ensure_session_token_header` in
  [`server_config.py`](../jvagent/cli/server_config.py)), so you do not need to
  add it manually via `JVSPATIAL_CORS_HEADERS` unless you are overriding the
  header list yourself — in that case include it with the defaults, e.g.
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
