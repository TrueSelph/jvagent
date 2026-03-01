# STT Action

Speech-to-Text action for jvagent that provides audio transcription capabilities using multiple providers.

## Overview

The STT Action converts audio to text using various speech recognition providers. It is a generic, provider-agnostic action usable by any adapter (WhatsApp, Telegram, web, etc.). Callers are responsible for passing the correct `audio_type` when known, since different sources use different formats (e.g. WhatsApp voice uses OGG-Opus). It follows jvagent action patterns with proper lifecycle hooks, error handling, and API endpoints.

## Features

- Convert audio from URLs to text
- Convert base64 encoded audio to text
- Convert audio file content to text with duration info
- Support for multiple audio formats (MP3, WAV, etc.)
- Health checking for service availability
- Modular provider system
- Async/await architecture
- Proper error handling and logging

## Supported Providers

### Deepgram
- High-quality speech recognition
- Multiple model options (nova-2, enhanced, nova, base)
- Smart formatting support
- Real-time and batch processing

## Configuration

```yaml
actions:
  - action: jvagent/stt_action
    context:
      enabled: true
      provider: deepgram
      api_key: ${DEEPGRAM_API_KEY}
      model: nova-2
      smart_format: true
```

## Usage

### Basic Transcription

```python
# Get STT action
stt_action = await self.get_action(STTAction)

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
  "provider": "deepgram",
  "model": "nova-2"
}
```

### GET /actions/{action_id}/stt/health
Check STT service health.

**Response:**
```json
{
  "healthy": true,
  "provider": "deepgram",
  "model": "nova-2"
}
```

## API Methods

### `invoke(audio_url: str) -> Optional[str]`
Convert speech from audio URL to text.

### `invoke_base64(audio_base64: str, audio_type: str = "audio/mp3") -> Optional[str]`
Convert speech from base64 encoded audio to text. Callers should pass `audio_type` when known; different sources use different formats (e.g. WhatsApp voice = `audio/ogg`). The default is for legacy use.

### `invoke_file(audio_content: bytes, audio_type: str = "audio/mp3") -> Optional[Dict]`
Convert speech from audio file content to text with duration info.

### `healthcheck() -> Union[bool, Dict[str, str]]`
Perform health check for the STT service.

## Dependencies

- `aiohttp>=3.8.0`
- `deepgram-sdk>=3.0.0`

## Environment Variables

- `DEEPGRAM_API_KEY`: Your Deepgram API key

## Error Handling

The action includes comprehensive error handling:
- Graceful degradation when API keys are missing
- Proper async exception handling
- Detailed error logging with stack traces
- Structured error responses for API endpoints