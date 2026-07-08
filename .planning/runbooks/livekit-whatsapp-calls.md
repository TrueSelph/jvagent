# Runbook: LiveKit WhatsApp voice calls

Enable inbound WhatsApp voice calls bridged to jvagent's Orchestrator via [LiveKit's WhatsApp Connector](https://docs.livekit.io/telephony/connectors/whatsapp/).

## 1. LiveKit project

### Option A — LiveKit Cloud (recommended)

1. Create a project at [livekit.io](https://livekit.io).
2. Copy **URL**, **API key**, and **API secret** from project settings.
3. Set env vars on jvagent and the voice worker:
   - `LIVEKIT_URL=wss://your-project.livekit.cloud`
   - `LIVEKIT_API_KEY=...`
   - `LIVEKIT_API_SECRET=...`

### Option B — Self-hosted

1. Run the official Docker generator: `docker run --rm -it -v$PWD:/output livekit/generate`
2. Use a public domain with TLS (Let's Encrypt).
3. Open firewall: TCP **7880**, **7881**, UDP **50000–60000**.
4. Save generated API key/secret and set `LIVEKIT_URL` to your `wss://` endpoint.

## 2. Meta / WhatsApp Calling API

1. Use `provider: meta` on `jvagent/whatsapp_action` (existing messaging setup).
2. In Meta Developer Console → WhatsApp → Configuration:
   - Same **Callback URL** as messages: `{JVAGENT_PUBLIC_BASE_URL}/api/whatsapp/interact/webhook/{agent_id}`
   - Subscribe to **`calls`** (in addition to `messages`).
   - Use Cloud API version **v23.0** or **v24.0** consistently.
3. Enable **Calling** on the business phone number and configure call hours.
4. Ensure `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_ACCESS_TOKEN`, and `WHATSAPP_APP_SECRET` are set.

## 3. jvagent agent.yaml

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

Install LiveKit Connector support on jvagent:

```bash
pip install "jvagent[livekit]"
```

## 4. Voice worker

The worker is a **standalone project** in `workers/livekit_voice/` (separate repo / Dokploy deploy — not the jvagent HTTP server).

```bash
cd workers/livekit_voice
pip install -r requirements.txt
cp .env.example .env

export LIVEKIT_URL=...
export LIVEKIT_API_KEY=...
export LIVEKIT_API_SECRET=...
export DEEPGRAM_API_KEY=...
export ELEVENLABS_API_KEY=...

python main.py dev
```

Or deploy with Docker: `docker compose up -d`. See [`workers/livekit_voice/README.md`](../../workers/livekit_voice/README.md).

`agent_name` must match `LiveKitWhatsAppAction.agent_name` (default `jvagent-voice`). The jvagent host and agent id are not worker env vars — they arrive per call in dispatch metadata (set `JVAGENT_PUBLIC_BASE_URL` on the jvagent side).

## 5. Verify

1. `GET /api/actions/{livekit_action_id}/livekit/status` — `configured: true`
2. Place a test call to the WhatsApp business number.
3. Check jvagent logs for `Accepted WhatsApp call` and worker logs for room join.
4. On hangup, logs should show `DisconnectWhatsAppCall`.

## 6. Troubleshooting

| Symptom | Check |
|---------|--------|
| Call rings then "Not Answered" | jvagent unreachable from Meta; accept must complete within ~60s |
| Call connects, silence | Voice worker not running or `agent_name` mismatch |
| Call rejected ("missing jvagent dispatch metadata") | jvagent didn't send `jvagent_base_url` / `jvagent_agent_id`; set `JVAGENT_PUBLIC_BASE_URL` (or action `jvagent_base_url`) |
| Agent speaks but wrong brain | jvagent host in dispatch metadata must reach jvagent `/interact` |
| Voicenotes broken | Unrelated — still use `stt_action` / `tts_action` on WhatsAppAction |

Further detail: [`jvagent/action/livekit_whatsapp/README.md`](../../jvagent/action/livekit_whatsapp/README.md).
