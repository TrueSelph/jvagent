# jvvoice

Standalone LiveKit agent that bridges WhatsApp call audio to the jvagent Orchestrator. Streams realtime audio (Deepgram STT + ElevenLabs TTS) and forwards each user turn to `POST /api/agents/{id}/interact`.

Designed as its own git repository and deployed separately from jvagent (e.g. on Dokploy).

## Architecture

```
WhatsApp caller → Meta → jvagent (LiveKitWhatsAppAction) → LiveKit room
                                                              ↓
                                                           jvvoice
                                                              ↓
                                              jvagent Orchestrator /interact
```

## Quick start (local)

```bash
cd workers/jvvoice   # or your standalone repo root after extract
pip install -r requirements.txt
cp .env.example .env
python main.py dev
```

## Environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| `LIVEKIT_URL` | Yes | LiveKit WebSocket URL (`wss://...`) — Cloud or self-hosted |
| `LIVEKIT_API_KEY` | Yes | LiveKit API key |
| `LIVEKIT_API_SECRET` | Yes | LiveKit API secret |
| `DEEPGRAM_API_KEY` | Yes | Deepgram STT |
| `ELEVENLABS_API_KEY` | Yes | ElevenLabs TTS |
| `LIVEKIT_AGENT_NAME` | No | LiveKit registration name (default `jvvoice`; must match jvagent action) |
| `DEEPGRAM_STT_MODEL` | No | Default `nova-3` |
| `ELEVENLABS_TTS_MODEL` | No | Default `eleven_turbo_v2_5` |
| `ELEVENLABS_VOICE_ID` | No | ElevenLabs voice id |

The jvagent host (`jvagent_base_url`) and agent id (`jvagent_agent_id`) are **not** configured here. Each jvagent instance sends them per call in LiveKit dispatch metadata. A call missing either is **rejected** (logged + job shutdown).

```
{jvagent_base_url}/api/agents/{jvagent_agent_id}/interact
```

`LIVEKIT_AGENT_NAME` is a startup registration value — it cannot come from per-call metadata because LiveKit needs it before dispatching jobs.

### Multiple jvagent hosts (shared jvvoice)

One deployed jvvoice instance can serve many jvagent hosts. Each jvagent's `LiveKitWhatsAppAction` includes `jvagent_base_url` in dispatch metadata when accepting a call.

Requirements:
- All jvagent instances use the **same LiveKit project** and dispatch to the **same `LIVEKIT_AGENT_NAME`**
- Each jvagent has `JVAGENT_PUBLIC_BASE_URL` (or `jvagent_base_url` in action context) set to a URL jvvoice can reach

### LiveKit Cloud vs self-hosted

jvvoice connects to whatever `LIVEKIT_URL` points at (Cloud or self-hosted). The **WhatsApp Connector** (`AcceptWhatsAppCall` on the jvagent side) is currently **LiveKit Cloud only** — self-hosted LiveKit works for the agent runtime but not for inbound WhatsApp calls today.

## Docker

```bash
cp .env.example .env
docker compose build
docker compose up -d
```

The Dockerfile runs `python main.py download-files` at build time to bake Silero VAD and turn-detector weights.

## Deploy on Dokploy

1. Create a new **Application** from this repository (copy `workers/jvvoice/` to a new repo root, or deploy from this path).
2. **Build type**: Dockerfile or `docker-compose.yml`.
3. Set environment variables (minimum: `LIVEKIT_*`, `DEEPGRAM_API_KEY`, `ELEVENLABS_API_KEY`).
4. Set `LIVEKIT_AGENT_NAME=jvvoice` (or match `agent_name` on `jvagent/livekit_whatsapp_action`).
5. Deploy. No inbound HTTP port required — jvvoice connects outbound to LiveKit and jvagent.

## Extract to own repo

```bash
cp -r workers/jvvoice /path/to/jvvoice-repo
cd /path/to/jvvoice-repo
git init
cp .env.example .env
docker compose build
pytest tests/ -v
```

Then remove `workers/jvvoice/` from the jvagent repo and update doc links to your new repo URL.

## jvagent side (separate deploy)

Enable `jvagent/livekit_whatsapp_action` with matching LiveKit credentials and `agent_name: jvvoice`. See:

- `jvagent/action/livekit_whatsapp/README.md`
- `.planning/runbooks/livekit-whatsapp-calls.md`

## Commands

| Command | Purpose |
|---------|---------|
| `python main.py dev` | Local development |
| `python main.py start` | Production |
| `python main.py download-files` | Pre-download model files (Docker build) |

## Tests

```bash
pip install -r requirements.txt pytest
pytest tests/ -v
```
