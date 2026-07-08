# Runbook: WhatsApp voice calls (via jvvoice)

Enable inbound WhatsApp voice calls bridged to jvagent's Orchestrator via **jvvoice**.

jvagent delegates call accept/disconnect to jvvoice — voice-backend credentials live on jvvoice only.

## 1. jvvoice backend (today: LiveKit Cloud)

jvvoice currently uses LiveKit's WhatsApp Connector. Deploy jvvoice with:

```bash
LIVEKIT_URL=wss://your-project.livekit.cloud
LIVEKIT_API_KEY=...
LIVEKIT_API_SECRET=...
LIVEKIT_AGENT_NAME=jvvoice
JVVOICE_API_KEY=shared-secret
JVVOICE_API_PORT=8080
DEEPGRAM_API_KEY=...
ELEVENLABS_API_KEY=...
```

WhatsApp Connector is **Cloud-only** today. See the jvvoice repo README for Dokploy setup (expose port **8080**).

## 2. Meta / WhatsApp Calling API (jvagent)

1. Use `provider: meta` on `jvagent/whatsapp_action`.
2. Subscribe to **`calls`** on the same webhook URL as messages.
3. Enable **Calling** on the business phone number.
4. Set `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_APP_SECRET`.

## 3. jvagent agent.yaml

```yaml
- action: jvagent/whatsapp_action
  context:
    provider: meta
    phone_number_id: "..."
    access_token: "..."

- action: jvagent/whatsapp_voice_action
  context:
    enabled: true
    jvvoice_base_url: "${JVVOICE_BASE_URL}"
    jvvoice_api_key: "${JVVOICE_API_KEY}"
    agent_name: jvvoice
    cloud_api_version: "24.0"
```

jvagent env:

```bash
JVAGENT_PUBLIC_BASE_URL=https://your-jvagent-host
JVVOICE_BASE_URL=https://jvvoice.yourdomain.com
JVVOICE_API_KEY=shared-secret
```

## 4. Verify

1. `GET /api/actions/{voice_action_id}/voice/status` on jvagent — `configured: true`
2. `GET https://jvvoice-host/health` — `{"status":"ok"}`
3. Place a test WhatsApp call.
4. jvagent logs: `Delegated WhatsApp call accept`; jvvoice logs: room join + interact POSTs.

## 5. Troubleshooting

| Symptom | Check |
|---------|--------|
| Call rings then "Not Answered" | jvagent cannot reach jvvoice API within ~60s |
| 401 from jvvoice | `JVVOICE_API_KEY` mismatch |
| Call connects, silence | jvvoice worker down or `agent_name` mismatch |
| Call rejected on worker | `JVAGENT_PUBLIC_BASE_URL` not set on jvagent |
| Empty replies | jvvoice cannot reach jvagent `/interact` |

Further detail: [`jvagent/action/whatsapp_voice/README.md`](../../jvagent/action/whatsapp_voice/README.md).
