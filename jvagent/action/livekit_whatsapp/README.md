# LiveKit WhatsApp Voice Calls

Bridge inbound WhatsApp voice calls to jvagent's Orchestrator via [LiveKit's WhatsApp Connector](https://docs.livekit.io/telephony/connectors/whatsapp/).

## Architecture

1. **jvagent** (`LiveKitWhatsAppAction`) receives Meta `field=calls` webhooks on the existing WhatsApp callback URL and calls LiveKit `AcceptWhatsAppCall`.
2. **LiveKit** creates a room and dispatches **jvvoice** (`agent_name`, default `jvvoice`).
3. **jvvoice** (standalone project under `workers/jvvoice/`) streams audio through Deepgram STT and ElevenLabs TTS; each user turn is sent to `POST /api/agents/{id}/interact` (Orchestrator). See [`workers/jvvoice/README.md`](../../../workers/jvvoice/README.md).
4. On call end, Meta sends `terminate` → jvagent calls `DisconnectWhatsAppCall`.

Voicenotes (PTT) still use `DeepgramSTTAction` / `ElevenLabsTTSAction` on the messaging path — not jvvoice.

## Prerequisites

### LiveKit

- **LiveKit Cloud** (required for WhatsApp Connector today): create a project at [livekit.io](https://livekit.io) and note `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`.
- **Self-hosted**: the jvvoice runtime can connect to a self-hosted `wss://` endpoint, but the WhatsApp Connector API is Cloud-only as of 2026.

### Meta / WhatsApp

- Existing Meta Cloud API setup (`provider: meta` on `WhatsAppAction`).
- Subscribe to the **`calls`** webhook field on the same callback URL as messages.
- Enable **Calling API** on the business phone number and configure call hours.
- Cloud API version **23.0** or **24.0** (set on `LiveKitWhatsAppAction.cloud_api_version`).

### jvagent

```yaml
- action: jvagent/whatsapp_action
  context:
    provider: meta
    phone_number_id: "..."
    access_token: "..."

- action: jvagent/livekit_whatsapp_action
  context:
    enabled: true
    agent_name: jvvoice
    cloud_api_version: "24.0"
```

## Environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| `LIVEKIT_URL` | Yes | LiveKit server WebSocket URL |
| `LIVEKIT_API_KEY` | Yes | LiveKit API key |
| `LIVEKIT_API_SECRET` | Yes | LiveKit API secret |
| `JVAGENT_PUBLIC_BASE_URL` | Yes | Public jvagent URL (Meta webhooks; sent to jvvoice as `jvagent_base_url` when action `jvagent_base_url` is empty) |
| `LIVEKIT_AGENT_NAME` | jvvoice | LiveKit registration name on jvvoice (must match `agent_name` above) |
| `DEEPGRAM_API_KEY` | jvvoice | Streaming STT |
| `ELEVENLABS_API_KEY` | jvvoice | Streaming TTS |
| `ELEVENLABS_VOICE_ID` | No | ElevenLabs voice ID (optional) |
| `WHATSAPP_*` | Yes | Same as messaging (phone_number_id, access_token, app_secret) |

## Run jvvoice

jvvoice is a **standalone project** in `workers/jvvoice/` (designed to be its own repo and deployed separately, e.g. on Dokploy).

```bash
cd workers/jvvoice
pip install -r requirements.txt
cp .env.example .env

export LIVEKIT_URL=wss://your-project.livekit.cloud
export LIVEKIT_API_KEY=...
export LIVEKIT_API_SECRET=...
export DEEPGRAM_API_KEY=...
export ELEVENLABS_API_KEY=...
export LIVEKIT_AGENT_NAME=jvvoice

python main.py dev
```

jvvoice does not take a jvagent host env var: each call carries its own `jvagent_base_url` in dispatch metadata. For production, deploy via Docker (`docker compose up`) or Dokploy. Full instructions: [`workers/jvvoice/README.md`](../../../workers/jvvoice/README.md).

### Shared jvvoice across multiple jvagent hosts

Each jvagent instance sends its own `jvagent_base_url` in LiveKit dispatch metadata (resolved from action `jvagent_base_url` or `JVAGENT_PUBLIC_BASE_URL` env). One shared jvvoice deployment routes calls to many jvagent hosts automatically. A call whose metadata omits `jvagent_base_url` or `jvagent_agent_id` is rejected.

## Install jvagent LiveKit extra

```bash
pip install "jvagent[livekit]"
```

This adds `livekit-api` for `LiveKitWhatsAppAction` only. jvvoice is a separate standalone project — see `workers/jvvoice/`.

## Troubleshooting

- **Call not answered within 60s**: ensure jvagent is reachable from Meta and `AcceptWhatsAppCall` runs in the webhook handler (not deferred).
- **No audio / no agent**: confirm jvvoice is running and `agent_name` / `LIVEKIT_AGENT_NAME` match.
- **Call rejected / "missing jvagent dispatch metadata"**: the accept step didn't send `jvagent_base_url` / `jvagent_agent_id` — set `JVAGENT_PUBLIC_BASE_URL` (or the action's `jvagent_base_url`) on the jvagent agent.
- **Empty agent replies**: check the jvagent host in dispatch metadata is reachable from jvvoice and inspect Orchestrator logs for `/interact` errors.
