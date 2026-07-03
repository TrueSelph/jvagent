# LiveKit WhatsApp Voice Calls

Bridge inbound WhatsApp voice calls to jvagent's Orchestrator via [LiveKit's WhatsApp Connector](https://docs.livekit.io/telephony/connectors/whatsapp/).

## Architecture

1. **jvagent** (`LiveKitWhatsAppAction`) receives Meta `field=calls` webhooks on the existing WhatsApp callback URL and calls LiveKit `AcceptWhatsAppCall`.
2. **LiveKit** creates a room and dispatches the voice worker (`agent_name`, default `jvagent-voice`).
3. **Voice worker** (`workers/livekit_voice/`) streams audio through Deepgram STT and ElevenLabs TTS; each user turn is sent to `POST /api/agents/{id}/interact` (Orchestrator).
4. On call end, Meta sends `terminate` → jvagent calls `DisconnectWhatsAppCall`.

Voicenotes (PTT) still use `DeepgramSTTAction` / `ElevenLabsTTSAction` on the messaging path — not this worker.

## Prerequisites

### LiveKit

- **LiveKit Cloud** (recommended): create a project at [livekit.io](https://livekit.io) and note `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`.
- **Self-hosted**: run LiveKit server with public TLS; open TCP 7880/7881 and UDP 50000–60000.

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
    agent_name: jvagent-voice
    cloud_api_version: "24.0"
```

## Environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| `LIVEKIT_URL` | Yes | LiveKit server WebSocket URL |
| `LIVEKIT_API_KEY` | Yes | LiveKit API key |
| `LIVEKIT_API_SECRET` | Yes | LiveKit API secret |
| `JVAGENT_PUBLIC_BASE_URL` | Yes | Public jvagent URL (Meta webhooks) |
| `JVAGENT_INTERNAL_BASE_URL` | No | Worker → jvagent URL if different from public |
| `JVAGENT_VOICE_AGENT_NAME` | No | Worker dispatch name (default `jvagent-voice`) |
| `DEEPGRAM_API_KEY` | Worker | Streaming STT |
| `ELEVENLABS_API_KEY` | Worker | Streaming TTS |
| `ELEVENLABS_VOICE_ID` | No | ElevenLabs voice ID (optional) |
| `WHATSAPP_*` | Yes | Same as messaging (phone_number_id, access_token, app_secret) |

## Run the voice worker

```bash
pip install "jvagent[livekit-voice]"
# or: pip install -r workers/livekit_voice/requirements.txt

export LIVEKIT_URL=wss://your-project.livekit.cloud
export LIVEKIT_API_KEY=...
export LIVEKIT_API_SECRET=...
export DEEPGRAM_API_KEY=...
export ELEVENLABS_API_KEY=...
export JVAGENT_PUBLIC_BASE_URL=https://your-jvagent-host

python -m workers.livekit_voice.main dev
```

For production, deploy the worker to LiveKit Cloud agent hosting or your own VM/K8s with the same env vars.

## Install jvagent LiveKit extra

```bash
pip install "jvagent[livekit]"
```

This adds `livekit-api` for `LiveKitWhatsAppAction` only. The voice worker needs `[livekit-voice]`.

## Troubleshooting

- **Call not answered within 60s**: ensure jvagent is reachable from Meta and `AcceptWhatsAppCall` runs in the webhook handler (not deferred).
- **No audio / no agent**: confirm the voice worker is running and `agent_name` matches `LiveKitWhatsAppAction.agent_name`.
- **Empty agent replies**: check `JVAGENT_INTERNAL_BASE_URL` from the worker and Orchestrator logs for `/interact` errors.
