# Model Action System

A lightweight, extensible LLM integration system for jvagent that provides both programmatic (library-style) and API interfaces for language model interactions.

## Features

- **Programmatic Interface**: Actions can call model actions directly as a library
- **API Interface**: HTTP endpoints wrapping programmatic calls
- **Multiple Providers**: OpenAI, OpenRouter, and extensible for custom providers
- **Sync & Streaming**: Both synchronous and streaming response modes
- **Standardized Results**: `ModelActionResult` works seamlessly for both modes
- **Token Tracking**: Automatic usage and cost estimation
- **Template System**: Jinja2-based prompt templating
- **Function Calling**: OpenAI-compatible tool/function calling
- **Action-Level Config**: Per-action configuration with agent overrides

## Architecture

### Core Components

1. **ModelActionResult**: Standardized result object supporting both sync and streaming
2. **ModelAction**: Base class defining the interface for all providers
3. **OpenAIModelAction**: OpenAI Chat Completions API implementation
4. **OpenRouterModelAction**: OpenRouter API implementation
5. **TemplateManager**: Jinja2-based prompt templating
6. **ToolManager**: Function calling support with validation

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
```

### Configuration

Set API keys in environment or `.env`:

```bash
export OPENAI_API_KEY="sk-..."
export OPENROUTER_API_KEY="sk-or-..."
```

## Usage

### Programmatic Usage (Action-to-Action)

#### Synchronous Query

```python
from jvagent.action.model import OpenAIModelAction

class MyAnalysisAction(Action):
    model_action_id: str = attribute(default="")
    
    async def analyze_text(self, text: str):
        # Get model action instance
        model = await OpenAIModelAction.get(self.model_action_id)
        
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
        model = await OpenAIModelAction.get(self.model_action_id)
        
        # Make streaming query
        result = await model.query_stream(
            prompt=f"Write a detailed report on: {topic}",
            temperature=0.7
        )
        
        # Stream chunks back to caller
        async for chunk in result.iter_stream():
            # Process chunk in real-time
            print(chunk, end="", flush=True)
        
        # Get metrics after streaming
        tokens = result.metrics.get('total_tokens', 'N/A')
        duration = result.metrics.get('duration', 'N/A')
        print(f"\nTokens used: {tokens}, Duration: {duration}s")
```

#### Using Templates

```python
from datetime import datetime

class MyTemplatedAction(Action):
    async def query_with_context(self, query: str, context: str):
        model = await OpenAIModelAction.get(self.model_action_id)
        
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
from jvagent.action.model.tools import create_weather_tool

class MyToolAction(Action):
    async def answer_with_tools(self, query: str):
        model = await OpenAIModelAction.get(self.model_action_id)
        
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
  archetype: OpenAIModelAction
  version: 1.0.0
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
from jvagent.action.model.tools import ToolDefinition

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

Extend `ModelAction` to add custom providers:

```python
from jvagent.action.model.base import ModelAction, ModelActionResult

class CustomModelAction(ModelAction):
    async def _query(self, messages, tools=None, **kwargs):
        # Implement sync query
        response = await self.call_custom_api(messages)
        return ModelActionResult(
            response=response,
            usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            model=self.model,
            provider="custom",
            duration=0.0
        )
    
    async def _query_stream(self, messages, tools=None, **kwargs):
        # Implement streaming query
        async def stream_gen():
            async for chunk in self.stream_custom_api(messages):
                yield chunk
        
        return ModelActionResult(
            stream=stream_gen(),
            usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            model=self.model,
            provider="custom",
            duration=0.0
        )
```

## Cost Estimation

Costs are automatically tracked based on token usage:

### OpenAI Pricing (per 1M tokens)

| Model | Input | Output |
|-------|-------|--------|
| gpt-4o | $2.50 | $10.00 |
| gpt-4o-mini | $0.15 | $0.60 |
| gpt-3.5-turbo | $0.50 | $1.50 |

Access costs via:
```python
print(f"Total cost: ${model.total_cost:.2f}")
print(f"Total tokens: {model.total_tokens}")
print(f"Requests: {model.total_requests}")
```

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
2. Extend `ModelAction`
3. Implement `_query()` and `_query_stream()`
4. Add provider-specific configuration attributes
5. Export in `__init__.py`

## License

Part of the jvagent project.

