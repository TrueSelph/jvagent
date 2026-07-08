# LiveKit WhatsApp Voice Calls

Bridge inbound WhatsApp voice calls to jvagent's Orchestrator via [jvvoice](https://github.com/your-org/jvvoice) and [LiveKit's WhatsApp Connector](https://docs.livekit.io/telephony/connectors/whatsapp/).

## Architecture

1. **jvagent** (`LiveKitWhatsAppAction`) receives Meta `field=calls` webhooks and delegates to **jvvoice** (`POST /api/calls/accept`).
2. **jvvoice** calls LiveKit `AcceptWhatsAppCall` (holds `LIVEKIT_*` secrets) and dispatches the voice worker.
3. **jvvoice worker** streams audio; each user turn is sent to `POST /api/agents/{id}/interact` on jvagent.
4. On call end, jvagent delegates `POST /api/calls/disconnect` to jvvoice.

jvagent does **not** need LiveKit credentials — only `JVVOICE_BASE_URL` and `JVVOICE_API_KEY`.

## Prerequisites

### jvvoice (separate deploy)

- Running jvvoice with connector API exposed (port 8080) and LiveKit worker registered as `jvvoice`.
- See the jvvoice repo README for Dokploy setup.

### Meta / WhatsApp

- Existing Meta Cloud API setup (`provider: meta` on `WhatsAppAction`).
- Subscribe to **`calls`** on the same webhook URL as messages.
- Enable **Calling API** on the business phone number.
- Cloud API version **23.0** or **24.0**.

### jvagent agent.yaml

```yaml
- action: jvagent/whatsapp_action
  context:
    provider: meta
    phone_number_id: "..."
    access_token: "..."

- action: jvagent/livekit_whatsapp_action
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
| `LIVEKIT_*` | jvvoice only | Yes | LiveKit Cloud credentials |
| `DEEPGRAM_API_KEY` | jvvoice | Yes | STT |
| `ELEVENLABS_API_KEY` | jvvoice | Yes | TTS |
| `WHATSAPP_*` | jvagent | Yes | Meta messaging/calling credentials |

## Install jvagent

jvagent no longer needs `pip install "jvagent[livekit]"` for voice calls — delegation uses httpx only. The `[livekit]` extra remains optional for other tooling.

## Troubleshooting

- **Call not answered within 60s**: jvagent must reach jvvoice API; accept must complete quickly.
- **401 from jvvoice**: `JVVOICE_API_KEY` mismatch between jvagent and jvvoice.
- **No audio / no agent**: jvvoice worker not running or `agent_name` / `LIVEKIT_AGENT_NAME` mismatch.
- **Call rejected on worker**: jvagent didn't send `jvagent_base_url` — set `JVAGENT_PUBLIC_BASE_URL`.
- **Empty agent replies**: jvvoice cannot reach jvagent `/interact` — check `JVAGENT_PUBLIC_BASE_URL` is reachable from jvvoice.
