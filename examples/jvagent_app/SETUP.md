# jvagent App Setup Guide

Quick setup guide for running your jvagent application with the OpenAI model action.

## Prerequisites

- Python 3.10+
- jvagent and jvspatial installed
- OpenAI API account

## Setup Steps

### 1. Environment Configuration

Copy the example environment file:

```bash
cp .env.example .env
```

Edit `.env` and add your OpenAI API key:

```bash
OPENAI_API_KEY=sk-your-actual-openai-api-key-here
```

Get your API key from: https://platform.openai.com/api-keys

### 2. Install Dependencies

```bash
cd /path/to/jvagent_app
pip install -r requirements.txt
```

If you don't have a requirements.txt, the dependencies are:
- jvagent
- jvspatial
- httpx>=0.27.0
- jinja2>=3.1.0

### 3. Run the Application

**Option 1: Using app root path (recommended)**
```bash
# From the jvagent repository root
jvagent examples/jvagent_app
```

**Option 2: From within the app directory**
```bash
cd examples/jvagent_app
jvagent
```

**With flags:**
```bash
# Update configurations
jvagent examples/jvagent_app --update

# Debug logging
jvagent examples/jvagent_app --debug

# Both
jvagent examples/jvagent_app --update --debug

# Run with serverless runtime simulation
jvagent examples/jvagent_app --serverless
```

## Using Core Actions

This example app uses core actions from the jvagent library. All configuration is done via `agent.yaml`

### Available Core Actions

- **`jvagent/interact_router`** - Unified posture classification + intent-based routing for InteractActions
- **`jvagent/openai_lm`** - OpenAI language model (multimodal support)
- **`jvagent/openai_embedding`** - OpenAI embedding model
- **`jvagent/typesense_vectorstore`** - Typesense vector store
- **`jvagent/retrieval_interact_action`** - Context retrieval from vector stores

### Using the Model Action

#### From API

Query the model action:

```bash
# Text query
curl -X POST http://localhost:8000/actions/{action_id}/query \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "Explain quantum computing in simple terms",
    "temperature": 0.7
  }'

# Vision query
curl -X POST http://localhost:8000/actions/{action_id}/query \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": [
      {"type": "text", "text": "What is in this image?"},
      {"type": "image_url", "image_url": {"url": "https://example.com/image.jpg"}}
    ]
  }'
```

#### From Another Action

```python
from jvagent.action.model.language.openai import OpenAILanguageModelAction

class MyAction(Action):
    async def my_method(self):
        # Get the model action (use actual ID or find by label)
        model = await OpenAILanguageModelAction.find_one({"context.label": "openai_lm"})

        # Text query
        result = await model.query_sync("Hello, how are you?")
        response = await result.get_response()

        # Vision query
        content = model.create_image_content(
            text="What's in this image?",
            image_url="https://example.com/image.jpg"
        )
        result = await model.query_sync(content)
        response = await result.get_response()
```

## Configuration

All actions are configured in `agents/jvagent/example_agent/agent.yaml`. Core actions are referenced directly without needing stub directories:

```yaml
actions:
  # Core action from jvagent library
  - action: jvagent/openai_lm
    context:
      enabled: true
      model: gpt-4o  # Change model here
      temperature: 0.7  # Adjust temperature
      max_tokens: 2000  # Adjust max tokens
      api_key: ${OPENAI_API_KEY}  # From .env file
```

The action loader will automatically:
1. Check for a local action at `actions/jvagent/openai_lm/`
2. If not found, load from core library at `jvagent/action/model/language/openai/`
3. Apply configuration from `agent.yaml` context

### Available Models

**Text + Vision (Multimodal)**:
- `gpt-4o` - Latest, best for vision (recommended)
- `gpt-4-turbo` - Fast GPT-4 with vision
- `gpt-4-vision-preview` - Vision preview

**Text Only**:
- `gpt-4o-mini` - Fast and cost-effective
- `gpt-3.5-turbo` - Older model (still supported)

## Troubleshooting

### "API key not found"
- Make sure `.env` file exists in the app directory
- Verify `OPENAI_API_KEY` is set correctly
- Restart the application after changing `.env`

### "Module not found" errors
- Install all dependencies: `pip install -r requirements.txt`
- Make sure jvagent and jvspatial are installed

### "Model not found" or "Invalid model"
- Check that you're using a valid OpenAI model name
- Verify your API key has access to the model
- For vision models, ensure you're using gpt-4o or gpt-4-vision-preview

### Vision queries not working
- Only use multimodal models (gpt-4o, gpt-4-turbo, gpt-4-vision-preview)
- Ensure image URLs are publicly accessible
- Check image format (JPEG, PNG, GIF, WebP)
- Verify image size is under 20MB

## Cost Management

Monitor your usage:

```bash
# Get metrics
curl http://localhost:8000/actions/{action_id}/metrics
```

Response:
```json
{
  "total_requests": 150,
  "total_tokens": 45000,
  "total_cost": 0.675,
  "model": "gpt-4o"
}
```

**Estimated costs (per 1M tokens)**:
- GPT-4o: $2.50 input, $10.00 output
- GPT-4o-mini: $0.15 input, $0.60 output
- GPT-3.5-turbo: $0.50 input, $1.50 output

Tips for cost reduction:
- Use `gpt-4o-mini` for simple queries
- Set lower `max_tokens` values
- Use `image_detail="low"` for vision queries
- Cache common responses

## Documentation

- Core action docs: `jvagent/action/model/language/openai/README.md`
- Core action docs: `jvagent/action/model/embedding/openai/README.md`
- Core action docs: `jvagent/action/router/README.md`
- Core action docs: `jvagent/action/retrieval/README.md`
- Core action docs: `jvagent/action/vectorstore/typesense/README.md`
- Main jvagent README: `../../README.md`

## Support

For issues or questions:
- Check the documentation files
- Review test examples in `tests/action/model/`
- OpenAI docs: https://platform.openai.com/docs

