# WhatsApp Voice Calls (via jvvoice)

Bridge inbound WhatsApp voice calls to jvagent's Orchestrator via [jvvoice](https://github.com/your-org/jvvoice).

## Architecture

1. **jvagent** (`WhatsAppVoiceAction`) receives Meta `field=calls` webhooks and delegates to **jvvoice** (`POST /api/calls/accept`).
2. On connect, jvagent resolves Meta credentials from **jvconnect** (`GET /api/v1/meta/whatsapp/calling/credentials`) and reuses the active WhatsApp text `session_id` (or creates a `channel=whatsapp` conversation).
3. **jvvoice** accepts the call, runs the voice worker, and bridges each user turn to `POST /api/agents/{id}/interact` on jvagent using that `session_id`.
4. On call end, jvagent delegates `POST /api/calls/disconnect` to jvvoice.

jvagent needs only `JVVOICE_BASE_URL`, `JVVOICE_API_KEY`, and the usual jvconnect WhatsApp settings — no local `WHATSAPP_ACCESS_TOKEN` for Meta provider.

## Prerequisites

### jvvoice (separate deploy)

- Running jvvoice with connector API exposed (port 8080) and worker registered as `jvvoice`.
- See the jvvoice repo README for Dokploy setup.

### Meta / WhatsApp (via jvconnect)

- Existing Meta Cloud API setup (`provider: meta` on `WhatsAppAction`) with `JVCONNECT_URL` + `JVCONNECT_API_KEY`.
- Subscribe to **`calls`** on the same webhook URL as messages (jvconnect forwards both).
- Enable **Calling API** on the business phone number.
- Cloud API version **23.0** or **24.0**.

### jvagent agent.yaml

```yaml
- action: jvagent/whatsapp_action
  context:
    provider: meta
    # phone/token come from jvconnect; optional yaml/env overrides for local/dev only

- action: jvagent/whatsapp_voice_action
  context:
    enabled: true
    jvvoice_base_url: "${JVVOICE_BASE_URL}"
    jvvoice_api_key: "${JVVOICE_API_KEY}"
    agent_name: jvvoice
    cloud_api_version: "24.0"
```

## Environment variables

| Variable | Side | Required | Description |
|----------|------|----------|-------------|
| `JVVOICE_BASE_URL` | jvagent | Yes | Public URL of jvvoice connector API |
| `JVVOICE_API_KEY` | both | Yes | Shared secret (`Authorization: Bearer`) |
| `JVAGENT_PUBLIC_BASE_URL` | jvagent | Yes | Sent to jvvoice as `jvagent_base_url` for `/interact` callbacks |
| `JVCONNECT_URL` / `JVCONNECT_API_KEY` | jvagent | Yes (meta) | Messaging + calling credentials via jvconnect |
| `WHATSAPP_PHONE_NUMBER_ID` / `WHATSAPP_ACCESS_TOKEN` | jvagent | No | Optional override; normally unused with jvconnect |

Voice-backend credentials (`LIVEKIT_*`, speech API keys, etc.) belong on **jvvoice only** — see the jvvoice repo.

## Session continuity

Voice calls share the same `Conversation.session_id` as the WhatsApp text chat for that caller when an active conversation exists. Cold calls create a new `channel=whatsapp` conversation (`sess_…`). Interact still uses `channel=whatsapp_call` so TTS replies do not also egress as WhatsApp text messages.

During a call, Orchestrator tools `whatsapp__send_flow` / `whatsapp__send_template` are allowed: they send to the caller's WhatsApp number (`user_id`) over the Cloud API while spoken replies stay on the voice path.

## Status endpoint

`GET /api/actions/{action_id}/voice/status` — returns `configured`, `agent_name`, `active_calls`.

## Troubleshooting

- **Call not answered within 60s**: jvagent must reach jvvoice API; accept must complete quickly.
- **401 from jvvoice**: `JVVOICE_API_KEY` mismatch between jvagent and jvvoice.
- **500 / Meta credentials required**: jvconnect `calling/credentials` failed or API key not phone-bound — check `JVCONNECT_*` and jvconnect logs.
- **No audio / no agent**: jvvoice worker not running or `agent_name` mismatch.
- **Call rejected on worker**: jvagent didn't send `jvagent_base_url` — set `JVAGENT_PUBLIC_BASE_URL`.
- **Empty agent replies**: jvvoice cannot reach jvagent `/interact` — check `JVAGENT_PUBLIC_BASE_URL` is reachable from jvvoice.
- **Call loses text context**: confirm jvagent is sending `session_id` on accept and jvvoice is on a build that forwards it.

## jvvoice implementation (today)

jvvoice currently uses [LiveKit's WhatsApp Connector](https://docs.livekit.io/telephony/connectors/whatsapp/) for call accept/disconnect and realtime audio. That is an implementation detail inside jvvoice — jvagent does not depend on LiveKit.
