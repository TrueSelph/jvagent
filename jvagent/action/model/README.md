# Model Action System

A lightweight, extensible language model integration system for jvagent that provides both programmatic (library-style) and API interfaces for language model interactions. Supports both text-only and multimodal (text + images) queries.

## Features

- **Programmatic Interface**: Actions can call model actions directly as a library
- **API Interface**: HTTP endpoints wrapping programmatic calls
- **Multiple Providers**: OpenAI, OpenRouter, and extensible for custom providers
- **Sync & Streaming**: Both synchronous and streaming response modes
- **Standardized Results**: `ModelActionResult` works seamlessly for both modes
- **ResponseBus Integration**: Direct publishing to ResponseBus for streaming and non-streaming responses
- **Token Tracking**: Automatic usage and cost estimation with token estimation for streaming calls
- **Observability Integration**: Automatic metrics emission to ResponseBus for interaction tracking
- **Template System**: Jinja2-based prompt templating
- **Function Calling**: OpenAI-compatible tool/function calling
- **Multimodal Support**: Text + images for visual understanding (LanguageModelAction implementations)
- **Action-Level Config**: Per-action configuration with agent overrides

## Architecture

### Package Structure

The `action/model` package is organized into subpackages to clearly separate concerns:

```
action/model/
├── base.py              # BaseModelAction (common base for all model types)
├── context.py          # Context variables for interaction_id propagation
├── utils/              # Utility modules
│   └── token_estimation.py  # Token estimation (tiktoken + word-based fallback)
├── language/           # Language model implementations and utilities
│   ├── __init__.py     # Package exports
│   ├── base.py         # LanguageModelAction base class and ModelActionResult
│   ├── openai/         # OpenAI implementation
│   │   └── openai.py
│   ├── openrouter/     # OpenRouter implementation
│   │   └── openrouter.py
│   ├── templates.py    # Template management (Jinja2)
│   ├── tools.py        # Function calling support
│   └── endpoints.py    # API endpoints for language models
└── embedding/          # Embedding model implementations
    ├── __init__.py     # Package exports
    ├── base.py         # EmbeddingModelAction base class
    ├── openai/         # OpenAI embeddings implementation
    │   └── openai.py
    ├── huggingface/    # HuggingFace Inference API implementation
    │   └── huggingface.py
    ├── openrouter/     # OpenRouter embeddings implementation
    │   └── openrouter.py
    └── generic/        # Generic RESTful API implementation
        └── generic.py
```

This structure provides clear separation between:
- **Language models**: Text generation and multimodal interactions
- **Embedding models**: Vector embedding generation
- **Shared base classes**: Common functionality for all model types

### Core Components

1. **BaseModelAction**: Generic base class with common attributes and operations (api_key, api_endpoint, model, timeout, metrics)
2. **ModelActionResult**: Standardized result object supporting both sync and streaming (language models only)
3. **LanguageModelAction**: Base class for language model actions (text generation and multimodal) extending BaseModelAction
4. **EmbeddingModelAction**: Base class for embedding model actions extending BaseModelAction
5. **OpenAILanguageModelAction**: OpenAI Chat Completions API implementation (extends LanguageModelAction)
6. **OpenRouterLanguageModelAction**: OpenRouter API implementation (extends LanguageModelAction)
7. **OpenAIEmbeddingModelAction**: OpenAI embeddings API implementation (extends EmbeddingModelAction)
8. **HuggingFaceEmbeddingModelAction**: HuggingFace Inference API implementation (extends EmbeddingModelAction)
9. **OpenRouterEmbeddingModelAction**: OpenRouter embeddings API implementation (extends EmbeddingModelAction)
10. **GenericEmbeddingModelAction**: Generic RESTful API implementation for custom embedding services
11. **TemplateManager**: Jinja2-based prompt templating (language models)
12. **ToolManager**: Function calling support with validation (language models)

### Class Hierarchy

```
Action (base)
└── BaseModelAction (generic base with common attributes/operations)
    ├── LanguageModelAction (text generation and multimodal - chat completions)
    │   ├── OpenAILanguageModelAction
    │   └── OpenRouterLanguageModelAction
    └── EmbeddingModelAction (embeddings)
        ├── OpenAIEmbeddingModelAction
        ├── HuggingFaceEmbeddingModelAction
        ├── OpenRouterEmbeddingModelAction
        └── GenericEmbeddingModelAction
```

### Design Principles

- **Programmatic First**: Core logic in Python methods, API wraps them
- **Unified Result**: Same result object for both usage patterns
- **Streaming Support**: AsyncGenerator works in-process and over HTTP
- **Action Composition**: Actions can easily call other model actions
- **No Langchain**: Lightweight, direct HTTP implementation

## Installation

### Dependencies

Add to your project's `requirements.txt`:

```
httpx>=0.27.0
jinja2>=3.1.0
tiktoken>=0.5.0  # Optional: for accurate token counting (recommended)
```

**Note**: `tiktoken` is optional but recommended for accurate token estimation in streaming mode. If not installed, the system falls back to word-based estimation.

### Configuration

Set API keys in environment or `.env`:

```bash
export OPENAI_API_KEY="sk-..."
export OPENROUTER_API_KEY="sk-or-..."
```

## Usage

### Programmatic Usage (Action-to-Action)

#### Using generate() with ResponseBus

The `generate()` method supports direct ResponseBus publishing when `response_bus` and `interaction` are provided:

```python
from jvagent.action.model import OpenAILanguageModelAction
from jvagent.memory import Interaction

class MyPersonaAction(Action):
    model_action_id: str = attribute(default="")

    async def respond(self, interaction: Interaction, visitor: Any):
        model = await OpenAILanguageModelAction.get(self.model_action_id)
        response_bus = getattr(visitor, "response_bus", None) if visitor else None

        # generate() will automatically publish to ResponseBus if provided
        response = await model.generate(
            prompt=interaction.utterance,
            stream=True,
            system="You are a helpful assistant",
            response_bus=response_bus,
            interaction=interaction,
            calling_action_name=self.get_class_name(),
        )

        return response
```

#### Using generate() without ResponseBus

For actions that don't need ResponseBus publishing (e.g., internal routing actions):

```python
from jvagent.action.model import OpenAILanguageModelAction

class MyRouterAction(Action):
    model_action_id: str = attribute(default="")

    async def route(self, prompt: str):
        model = await OpenAILanguageModelAction.get(self.model_action_id)

        # generate() without ResponseBus - just returns the response
        response = await model.generate(
            prompt=prompt,
            stream=False,
            system="You are a routing assistant",
            calling_action_name=self.get_class_name(),
        )

        return response
```

#### Using query_sync() and query_stream() (Lower-level API)

For more control over the result object:

```python
from jvagent.action.model import OpenAILanguageModelAction

class MyAnalysisAction(Action):
    model_action_id: str = attribute(default="")

    async def analyze_text(self, text: str):
        # Get model action instance
        model = await OpenAILanguageModelAction.get(self.model_action_id)

        # Make synchronous query
        result = await model.query_sync(
            prompt=f"Analyze this text: {text}",
            system="You are an expert analyst"
        )

        # Get complete response
        analysis = await result.get_response()
        tokens_used = result.metrics['total_tokens']
        duration = result.metrics.get('duration', 0)

        return {"analysis": analysis, "tokens": tokens_used, "duration": duration}
```

#### Streaming Query

```python
class MyStreamingAction(Action):
    async def generate_report(self, topic: str):
        model = await OpenAILanguageModelAction.get(self.model_action_id)

        # Make streaming query
        result = await model.query_stream(
            prompt=f"Write a detailed report on: {topic}",
            temperature=0.7
        )

        # Stream chunks back to caller
        async for chunk in result.iter_stream():
            # Process chunk in real-time
            print(chunk, end="", flush=True)

        # Get metrics after streaming (tokens are estimated after stream completes)
        tokens = result.metrics.get('total_tokens', 'N/A')
        duration = result.metrics.get('duration', 'N/A')
        is_estimated = getattr(result, '_usage_estimated', False)
        print(f"\nTokens used: {tokens} ({'estimated' if is_estimated else 'actual'}), Duration: {duration}s")
```

#### Using Templates

```python
from datetime import datetime

class MyTemplatedAction(Action):
    async def query_with_context(self, query: str, context: str):
        model = await OpenAILanguageModelAction.get(self.model_action_id)

        # Apply template
        formatted_prompt = await model.apply_template(
            "contextual_query",
            query=query,
            context=context,
            timestamp=datetime.now()
        )

        result = await model.query_sync(formatted_prompt)
        return await result.get_response()
```

#### Function Calling

```python
from jvagent.action.model.language.tools import create_weather_tool

class MyToolAction(Action):
    async def answer_with_tools(self, query: str):
        model = await OpenAILanguageModelAction.get(self.model_action_id)

        # Define tools
        weather_tool = create_weather_tool()
        tools = [weather_tool.to_dict()]

        # Query with tools
        result = await model.query_sync(
            prompt=query,
            tools=tools
        )

        # Check for tool calls
        if result.tool_calls:
            for call in result.tool_calls:
                # Execute tool and get result
                tool_result = await self.execute_tool(call)
                # Continue conversation with tool results...

        return await result.get_response()
```

### API Usage

**Note**: The `model` parameter can be passed in the request body to override the action's default model for a single query.

#### Synchronous Query

```bash
POST /actions/{action_id}/query
Content-Type: application/json

{
  "prompt": "Explain quantum computing",
  "system": "You are a physics expert",
  "model": "gpt-4o",
  "temperature": 0.7,
  "max_tokens": 500
}
```

Response:
```json
{
  "response": "Quantum computing is...",
  "metrics": {
    "prompt_tokens": 20,
    "completion_tokens": 150,
    "total_tokens": 170,
    "duration": 1.234
  },
  "model": "gpt-4o-mini",
  "provider": "openai",
  "finish_reason": "stop",
  "tool_calls": []
}
```

#### Streaming Query

```bash
POST /actions/{action_id}/query
Content-Type: application/json

{
  "prompt": "Tell me a story",
  "stream": true,
  "model": "gpt-4o-mini"
}
```

Response (Server-Sent Events):
```
data: {"delta": "Once", "metrics": null, "finish_reason": null}
data: {"delta": " upon", "metrics": null, "finish_reason": null}
...
data: {"delta": "", "metrics": {"prompt_tokens": 10, "completion_tokens": 200, "total_tokens": 210, "duration": 2.456}, "finish_reason": "stop", "tool_calls": []}
data: [DONE]
```

**Note**: For streaming queries, token counts are estimated after the stream completes using tiktoken (when available) or word-based estimation. The duration reflects the full time from query start to stream completion.

### Multimodal Query (Vision)

```bash
POST /actions/{action_id}/query
Content-Type: application/json

{
  "prompt": [
    {
      "type": "text",
      "text": "What's in this image? Describe it in detail."
    },
    {
      "type": "image_url",
      "image_url": {
        "url": "https://example.com/image.jpg",
        "detail": "high"
      }
    }
  ],
  "system": "You are an expert image analyst"
}
```

With base64-encoded image:
```bash
POST /actions/{action_id}/query
Content-Type: application/json

{
  "prompt": [
    {"type": "text", "text": "Analyze this image"},
    {
      "type": "image_url",
      "image_url": {
        "url": "data:image/jpeg;base64,/9j/4AAQSkZJRg..."
      }
    }
  ]
}
```

Multiple images:
```bash
POST /actions/{action_id}/query
Content-Type: application/json

{
  "prompt": [
    {"type": "text", "text": "Compare these images"},
    {"type": "image_url", "image_url": {"url": "https://example.com/img1.jpg"}},
    {"type": "image_url", "image_url": {"url": "https://example.com/img2.jpg"}}
  ]
}
```

#### Get Metrics

```bash
GET /actions/{action_id}/metrics
```

Response:
```json
{
  "total_requests": 150,
  "total_tokens": 45000,
  "total_cost": 0.675,
  "total_duration": 125.5,
  "average_duration": 0.837,
  "model": "gpt-4o-mini",
  "provider": "openai"
}
```

#### List Templates

```bash
GET /actions/{action_id}/templates
```

#### Render Template

```bash
POST /actions/{action_id}/templates/contextual_query/render
Content-Type: application/json

{
  "variables": {
    "query": "What is AI?",
    "context": "Machine learning basics"
  }
}
```

## Configuration

### Action-Level Configuration

In `info.yaml`:
```yaml
package:
  name: jvagent/model_openai
  archetype: OpenAILanguageModelAction
  version: 0.0.1
```

### Agent-Level Overrides

In `agent.yaml`:
```yaml
actions:
  - action: jvagent/model_openai
    context:
      api_key: ${OPENAI_API_KEY}
      model: gpt-4o-mini
      temperature: 0.7
      max_tokens: 1000
      timeout: 30
```

## Templates

### Creating Templates

Place Jinja2 templates in your action's `templates/` directory:

#### `templates/contextual_query.j2`
```jinja
{% if context %}
Context:
{{ context }}

{% endif %}Query: {{ query }}
```

#### `templates/system_prompt.j2`
```jinja
You are a helpful AI assistant.
You provide clear, accurate, and concise responses.
{% if domain %}You specialize in {{ domain }}.{% endif %}
```

### Using Templates

```python
prompt = await model.apply_template(
    "contextual_query",
    query="What is AI?",
    context="Machine learning fundamentals"
)
```

## Function Calling

### Defining Tools

```python
from jvagent.action.model.language.tools import ToolDefinition

tool = ToolDefinition(
    name="get_weather",
    description="Get current weather for a location",
    parameters={
        "type": "object",
        "properties": {
            "location": {
                "type": "string",
                "description": "City name"
            },
            "unit": {
                "type": "string",
                "enum": ["celsius", "fahrenheit"]
            }
        },
        "required": ["location"]
    }
)
```

### Using Tools

```python
result = await model.query_sync(
    prompt="What's the weather in San Francisco?",
    tools=[tool.to_dict()]
)

if result.tool_calls:
    for call in result.tool_calls:
        print(f"Tool: {call['function']['name']}")
        print(f"Args: {call['function']['arguments']}")
```

## Providers

### OpenAI

Default endpoint: `https://api.openai.com/v1`

Models:
- `gpt-4o` - Latest GPT-4 optimized (recommended)
- `gpt-4o-mini` - Fast and cost-effective (default)
- `gpt-3.5-turbo` - Older model (still supported)

Configuration:
```yaml
actions:
  - action: jvagent/model_openai
    context:
      api_key: ${OPENAI_API_KEY}
      model: gpt-4o-mini
```

### OpenRouter

Default endpoint: `https://openrouter.ai/api/v1`

Models:
- `openai/gpt-4o`
- `anthropic/claude-3.5-sonnet`
- `google/gemini-pro`
- Many more...

Configuration:
```yaml
actions:
  - action: jvagent/model_openrouter
    context:
      api_key: ${OPENROUTER_API_KEY}
      model: anthropic/claude-3.5-sonnet
      http_referer: https://yoursite.com
      site_name: YourApp
```

### Custom Providers

Extend `LanguageModelAction` to add custom language model providers:

```python
from jvagent.action.model.language.base import LanguageModelAction, ModelActionResult
from jvspatial.core.annotations import attribute

class CustomModelAction(LanguageModelAction):
    # Required: Set provider attribute
    provider: str = attribute(
        default="custom", description="Provider name"
    )

    async def _query(self, messages, tools=None, **kwargs):
        # Implement sync query
        response = await self.call_custom_api(messages)
        return ModelActionResult(
            response=response,
            usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            model=self.model,
            provider=self.provider,
            duration=0.0
        )

    async def _query_stream(self, messages, tools=None, **kwargs):
        # Implement streaming query
        async def stream_gen():
            async for chunk in self.stream_custom_api(messages):
                yield chunk

        result = ModelActionResult(
            stream=stream_gen(),
            usage={},  # Empty for streaming - will be estimated after completion
            model=self.model,
            provider=self.provider,
            duration=0.0
        )
        # Store messages for token estimation (handled automatically by base class)
        result._messages_for_estimation = messages
        return result
```

**Important**: All model action classes must define a `provider` attribute. For streaming queries, token estimation is handled automatically by the base class after stream completion.

## Token Estimation and Cost Tracking

### Token Counting

Token usage is automatically tracked for all model calls:

- **Synchronous calls**: Token counts come directly from the API response
- **Streaming calls**: Tokens are estimated after stream completion using:
  - **tiktoken** (when available): Accurate token counting for OpenAI/OpenRouter models
  - **Word-based fallback**: Approximate estimation (1.3x word count) when tiktoken is unavailable

Token estimation handles:
- Prompt tokens (from input messages)
- Completion tokens (from streamed response)
- Model-specific tokenization (GPT-4, GPT-3.5, Claude, etc.)
- OpenRouter model format (`provider/model`)

### Cost Estimation

Costs are automatically tracked based on token usage:

#### OpenAI Pricing (per 1M tokens)

| Model | Input | Output |
|-------|-------|--------|
| gpt-4o | $2.50 | $10.00 |
| gpt-4o-mini | $0.15 | $0.60 |
| gpt-3.5-turbo | $0.50 | $1.50 |

Access costs via:
```python
print(f"Total cost: ${model.total_cost:.2f}")
print(f"Total tokens: {model.total_tokens}")
print(f"Total requests: {model.total_requests}")
```

### Observability Metrics

Model calls automatically emit observability metrics to the ResponseBus when an interaction context is available. Metrics include:

- **Provider**: Model provider name (openai, openrouter, etc.)
- **Model**: Model identifier used (actual model from query result)
- **Usage**: Token counts (prompt_tokens, completion_tokens, total_tokens)
- **Duration**: Query duration in seconds (accurate for streaming, includes full stream time)
- **Estimated flag**: Indicates whether token counts are estimated (true for streaming) or actual (false for sync)
- **Action label**: The model action's label
- **Calling action label**: The label of the action that initiated the model call
- **System prompt**: The system prompt that was executed
- **User prompt**: The user's input prompt
- **Response**: Complete response text (when available)
- **Is streaming**: Whether the call was streaming
- **Finish reason**: Completion reason (stop, length, tool_calls, etc.)
- **Tool calls**: Function calls made (if any)

Metrics are aggregated in the Interaction node's `observability_metrics` field after interaction finalization.

## Testing

Run tests with pytest:

```bash
cd /path/to/jvagent
pytest tests/action/model/
```

## Examples

See the example action:
- `/jvagent_app/agents/jvagent/example_agent/actions/jvagent/model_openai/`

## Contributing

To add a new provider:
1. Create a new file in `jvagent/action/model/`
2. Extend `LanguageModelAction` for language model providers or `EmbeddingModelAction` for embedding providers
3. **Required**: Define a `provider` attribute with the provider name
4. Implement `_query()` and `_query_stream()` (for language models) or `_embed()` (for embeddings)
5. For streaming queries, ensure `ModelActionResult` includes `provider` and stores messages for token estimation
6. Add provider-specific configuration attributes
7. Export in `__init__.py`

Example provider attribute:
```python
from jvspatial.core.annotations import attribute

class MyModelAction(LanguageModelAction):
    provider: str = attribute(
        default="myprovider", description="Provider name"
    )
    # ... rest of implementation
```

## License

Part of the jvagent project.

