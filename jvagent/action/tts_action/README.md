# TTS Action

Text-to-Speech action for jvagent that provides speech synthesis capabilities using multiple providers.

## Features

- Convert text to speech audio
- Multiple output formats (bytes, base64, file URL)
- Voice and model selection
- Health checking for service availability
- Modular provider system

## Supported Providers

### ElevenLabs
- High-quality voice synthesis
- Multiple voice options
- Various model choices
- Real-time generation

## Configuration

```yaml
actions:
  - action: jvagent/tts_action
    context:
      provider: elevenlabs
      api_key: your_elevenlabs_api_key
      model: eleven_turbo_v2
      voice: Sarah
```

## Usage

### Basic Speech Synthesis

```python
# Get TTS action
tts_action = await self.get_action("TTSAction")

# Generate speech as bytes
audio_bytes = await tts_action.invoke("Hello, world!")

# Generate speech as base64
audio_base64 = await tts_action.invoke("Hello, world!", as_base64=True)

# Generate speech as file URL
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
for model in models:
    print(f"Model: {model['name']} - {model['description']}")
```

### Audio Processing

```python
# Convert existing audio bytes to different formats
audio_base64 = tts_action.get_audio_as(audio_bytes, as_base64=True)
audio_url = tts_action.get_audio_as(audio_bytes, as_url=True)
```

### Health Check

```python
health = await tts_action.healthcheck()
if health is True:
    print("TTS service is healthy")
else:
    print(f"TTS service error: {health['message']}")
```

## API Methods

### `invoke(text: str, as_base64: bool = False, as_url: bool = False) -> Optional[Union[str, bytes]]`
Convert text to speech audio.

### `get_audio_as(audio: bytes, as_base64: bool = False, as_url: bool = False) -> Optional[Union[str, bytes]]`
Convert audio bytes to different formats.

### `get_voices() -> List[Dict[str, str]]`
Get all available voices for the current provider.

### `get_models() -> List[Dict[str, str]]`
Get all available models for the current provider.

### `healthcheck() -> Union[bool, Dict[str, str]]`
Perform health check for the TTS service.

## Dependencies

- `requests>=2.25.0`
- `elevenlabs>=1.0.0`

## Environment Variables

- `ELEVENLABS_API_KEY`: Your ElevenLabs API key (alternative to config)