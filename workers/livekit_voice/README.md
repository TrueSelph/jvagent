# jvagent LiveKit Voice Worker

Standalone LiveKit agent worker for WhatsApp voice calls. Bridges realtime audio (Deepgram STT + ElevenLabs TTS) to the jvagent Orchestrator via `POST /api/agents/{id}/interact`.

This folder is designed to be its own git repository and deployed separately from jvagent (e.g. on Dokploy).

## Architecture

```
WhatsApp caller → Meta → jvagent (LiveKitWhatsAppAction) → LiveKit room
                                                              ↓
                                                    this voice worker
                                                              ↓
                                              jvagent Orchestrator /interact
```

## Quick start (local)

```bash
cd workers/livekit_voice   # or your standalone repo root
pip install -r requirements.txt
cp .env.example .env       # fill in keys
python main.py dev
```

## Environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| `LIVEKIT_URL` | Yes | LiveKit Cloud WebSocket URL (`wss://...`) |
| `LIVEKIT_API_KEY` | Yes | LiveKit API key |
| `LIVEKIT_API_SECRET` | Yes | LiveKit API secret |
| `DEEPGRAM_API_KEY` | Yes | Deepgram STT |
| `ELEVENLABS_API_KEY` | Yes | ElevenLabs TTS |
| `JVAGENT_BASE_URL` | Yes | Base URL of your jvagent server (no trailing slash) |
| `JVAGENT_AGENT_ID` | Fallback | Used when LiveKit job metadata omits `jvagent_agent_id` |
| `JVAGENT_VOICE_AGENT_NAME` | No | Worker name (default `jvagent-voice`; must match jvagent action) |
| `DEEPGRAM_STT_MODEL` | No | Default `nova-3` |
| `ELEVENLABS_TTS_MODEL` | No | Default `eleven_turbo_v2_5` |
| `ELEVENLABS_VOICE_ID` | No | ElevenLabs voice id |

After deploying jvagent, set `JVAGENT_BASE_URL` to its public URL (e.g. `https://desk8800.example.com`). The worker calls:

```
{JVAGENT_BASE_URL}/api/agents/{agent_id}/interact
```

## Docker

```bash
cp .env.example .env   # configure for your environment
docker compose build
docker compose up -d
```

The Dockerfile runs `python main.py download-files` at build time to bake Silero VAD and turn-detector weights.

## Deploy on Dokploy

1. Create a new **Application** from this repository (or copy this folder into a new repo).
2. **Build type**: Dockerfile (path `Dockerfile`) or Docker Compose (`docker-compose.yml`).
3. Set environment variables in Dokploy (same as `.env.example`). At minimum:
   - `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`
   - `DEEPGRAM_API_KEY`, `ELEVENLABS_API_KEY`
   - `JVAGENT_BASE_URL` → your deployed jvagent URL
4. Ensure `JVAGENT_VOICE_AGENT_NAME` matches `agent_name` on `jvagent/livekit_whatsapp_action` in your jvagent app.
5. Deploy. The worker registers with LiveKit Cloud and accepts jobs dispatched from jvagent when WhatsApp calls connect.

No inbound HTTP port is required — the worker connects outbound to LiveKit and jvagent.

## jvagent side (separate deploy)

Keep `jvagent/livekit_whatsapp_action` enabled in your jvagent agent with the same LiveKit project credentials. See jvagent docs:

- `jvagent/action/livekit_whatsapp/README.md`
- `.planning/runbooks/livekit-whatsapp-calls.md` (in the jvagent repo)

## Commands

| Command | Purpose |
|---------|---------|
| `python main.py dev` | Local development worker |
| `python main.py start` | Production worker |
| `python main.py download-files` | Pre-download model files (Docker build) |

## Tests

```bash
pip install -r requirements.txt pytest
pytest tests/ -v
```
