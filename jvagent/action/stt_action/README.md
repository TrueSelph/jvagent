# STT Action

Speech-to-Text action for jvagent that provides audio transcription capabilities using multiple providers.

## Overview

The STT action follows the web_search architectural pattern: an abstract `BaseSTTAction` defines the protocol, and concrete providers (e.g. `DeepgramSTTAction`) are separate packages. Each provider is a first-class Action that agents register directly. It is generic and provider-agnostic; callers (WhatsApp, Telegram, etc.) pass the correct `audio_type` when known.

## Architecture

- **BaseSTTAction**: Abstract base class defining invoke, invoke_base64, invoke_file, healthcheck
- **DeepgramSTTAction**: Concrete implementation using the Deepgram API (package: jvagent/deepgram_stt)

## Supported Providers

| Provider | Package | Class |
|----------|---------|-------|
| Deepgram | jvagent/deepgram_stt | DeepgramSTTAction |

## Configuration

```yaml
actions:
  - action: jvagent/deepgram_stt
    context:
      enabled: true
      api_key: ${DEEPGRAM_API_KEY}
      model: nova-2
      smart_format: true
```

## Usage

### Basic Transcription

```python
# Get STT action by class name
stt_action = await self.get_action("DeepgramSTTAction")

# Transcribe from URL
transcript = await stt_action.invoke("https://example.com/audio.mp3")

# Transcribe from base64 (pass audio_type when known for best results)
transcript = await stt_action.invoke_base64(audio_base64, "audio/ogg")  # e.g. WhatsApp PTT

# Transcribe from file content with duration
result = await stt_action.invoke_file(audio_bytes, "audio/wav")
# Returns: {"transcript": "...", "duration": 12.5}
```

### Health Check

```python
health = await stt_action.healthcheck()
if health is True:
    print("STT service is healthy")
else:
    print(f"STT service error: {health['message']}")
```

## API Endpoints

### POST /actions/{action_id}/stt/transcribe
Transcribe audio to text.

**Request:**
```json
{
  "audio_url": "https://example.com/audio.mp3"
}
```

**Response:**
```json
{
  "success": true,
  "transcript": "Hello world",
  "provider": "DeepgramSTTAction",
  "model": "nova-2"
}
```

### GET /actions/{action_id}/stt/health
Check STT service health.

## API Methods

### `invoke(audio_url: str) -> Optional[str]`
Convert speech from audio URL to text.

### `invoke_base64(audio_base64: str, audio_type: str = "audio/mp3") -> Optional[str]`
Convert speech from base64 encoded audio to text. Callers should pass `audio_type` when known; different sources use different formats (e.g. WhatsApp voice = `audio/ogg`).

### `invoke_file(audio_content: bytes, audio_type: str = "audio/mp3") -> Optional[Dict]`
Convert speech from audio file content to text with duration info.

### `healthcheck() -> Union[bool, Dict[str, str]]`
Perform health check for the STT service.

## Dependencies

- Deepgram: `deepgram-sdk>=6.0.0`

## Environment Variables

- `DEEPGRAM_API_KEY`: Your Deepgram API key
