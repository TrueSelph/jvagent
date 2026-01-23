# STT Action

Speech-to-Text action for jvagent that provides audio transcription capabilities using multiple providers.

## Features

- Convert audio from URLs to text
- Convert base64 encoded audio to text
- Support for multiple audio formats (MP3, WAV, etc.)
- Health checking for service availability
- Modular provider system

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
      provider: deepgram
      api_key: your_deepgram_api_key
      model: nova-2
      smart_format: true
```

## Usage

### Basic Transcription

```python
# Get STT action
stt_action = await self.get_action("STTAction")

# Transcribe from URL
transcript = await stt_action.invoke("https://example.com/audio.mp3")

# Transcribe from base64
transcript = await stt_action.invoke_base64(audio_base64, "audio/mp3")

# Transcribe from file content
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

## API Methods

### `invoke(audio_url: str) -> Optional[str]`
Convert speech from audio URL to text.

### `invoke_base64(audio_base64: str, audio_type: str = "audio/mp3") -> Optional[str]`
Convert speech from base64 encoded audio to text.

### `invoke_file(audio_content: bytes, audio_type: str = "audio/mp3") -> Optional[Dict]`
Convert speech from audio file content to text with duration info.

### `healthcheck() -> Union[bool, Dict[str, str]]`
Perform health check for the STT service.

## Dependencies

- `requests>=2.25.0`
- `deepgram-sdk>=3.0.0`

## Environment Variables

- `DEEPGRAM_API_KEY`: Your Deepgram API key (alternative to config)