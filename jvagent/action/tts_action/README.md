# TTS Action

Text-to-Speech action for jvagent that provides speech synthesis capabilities using multiple providers.

## Overview

The TTS action follows the web_search architectural pattern: an abstract `BaseTTSAction` defines the protocol, and concrete providers (e.g. `ElevenLabsTTSAction`) are separate packages. Each provider is a first-class Action that agents register directly. It is generic and provider-agnostic; use `as_url=True` when output will be sent to adapters (e.g. WhatsApp).

## Architecture

- **BaseTTSAction**: Abstract base class defining invoke, get_audio_as, healthcheck; optional get_voices, get_voice_by_name, get_models
- **ElevenLabsTTSAction**: Concrete implementation using the ElevenLabs API (package: jvagent/elevenlabs_tts)

## Supported Providers

| Provider | Package | Class |
|----------|---------|-------|
| ElevenLabs | jvagent/elevenlabs_tts | ElevenLabsTTSAction |

## Configuration

```yaml
actions:
  - action: jvagent/elevenlabs_tts
    context:
      enabled: true
      api_key: ${ELEVENLABS_API_KEY}
      model: eleven_turbo_v2
      voice: Sarah
```

## Usage

### Basic Speech Synthesis

```python
# Get TTS action by class name
tts_action = await self.get_action("ElevenLabsTTSAction")

# Generate speech as bytes
audio_bytes = await tts_action.invoke("Hello, world!")

# Generate speech as base64 (for inline use, e.g. web players)
audio_base64 = await tts_action.invoke("Hello, world!", as_base64=True)

# Generate speech as file URL (for adapters that fetch and send the file)
audio_url = await tts_action.invoke("Hello, world!", as_url=True)
```

### Voice and Model Management

```python
# Get available voices
voices = await tts_action.get_voices()
for voice in voices:
    print(f"Voice: {voice['name']} (ID: {voice['voice_id']})")

# Get available models
models = await tts_action.get_models()
```

### Health Check

```python
health = await tts_action.healthcheck()
if health is True:
    print("TTS service is healthy")
else:
    print(f"TTS service error: {health['message']}")
```

## API Endpoints

### POST /actions/{action_id}/tts/synthesize
Synthesize speech from text.

**Request:**
```json
{
  "text": "Hello, world!",
  "as_base64": false,
  "as_url": true
}
```

**Response:**
```json
{
  "success": true,
  "audio": "https://example.com/audio.mp3",
  "format": "url",
  "provider": "ElevenLabsTTSAction",
  "model": "eleven_turbo_v2",
  "voice": "Sarah"
}
```

### GET /actions/{action_id}/tts/voices
Get available voices.

### GET /actions/{action_id}/tts/models
Get available models.

### GET /actions/{action_id}/tts/health
Check TTS service health.

## API Methods

### `invoke(text: str, as_base64: bool = False, as_url: bool = False) -> Optional[Union[str, bytes]]`
Convert text to speech. Use `as_url=True` when output will be sent to adapters (e.g. WhatsApp). Use `as_base64=True` for inline audio (e.g. web players).

### `get_audio_as(audio: bytes, as_base64: bool = False, as_url: bool = False) -> Optional[Union[str, bytes]]`
Convert audio bytes to different formats.

### `get_voices() -> List[Dict[str, str]]`
Get all available voices (if supported by provider).

### `get_models() -> List[Dict[str, str]]`
Get all available models (if supported by provider).

### `healthcheck() -> Union[bool, Dict[str, str]]`
Perform health check for the TTS service.

## Dependencies

- ElevenLabs: `elevenlabs>=1.13.0`

## Environment Variables

- `ELEVENLABS_API_KEY`: Your ElevenLabs API key
