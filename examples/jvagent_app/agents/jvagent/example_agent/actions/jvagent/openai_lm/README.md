# OpenAI Model Action

LLM integration for OpenAI models with support for both synchronous and streaming responses.

## Features

- **Multiple Models**: Support for GPT-4, GPT-4o, GPT-3.5-turbo, and other OpenAI models
- **Sync & Streaming**: Both synchronous queries and streaming responses
- **Programmatic Interface**: Call directly from other actions as a library
- **API Endpoints**: HTTP endpoints for external access
- **Token Tracking**: Automatic token usage and cost estimation
- **Template Support**: Jinja2-based prompt templating
- **Function Calling**: Support for OpenAI function/tool calling

## Configuration

Configure in your agent.yaml:

```yaml
actions:
  - action: jvagent/openai_lm
    context:
      api_key: ${OPENAI_API_KEY}  # From environment variable
      model: gpt-4o-mini
      temperature: 0.7
      max_tokens: 1000
      timeout: 30
```

## Programmatic Usage

### From Another Action

```python
from jvagent.action.model import OpenAILanguageModelAction

class MyAction(Action):
    # Reference to the model action ID
    model_action_id: str = attribute(default="")
    
    async def analyze_text(self, text: str):
        # Get the model action instance
        model = await OpenAILanguageModelAction.get(self.model_action_id)
        
        # Synchronous query
        result = await model.query_sync(
            prompt=f"Analyze this text: {text}",
            system="You are an expert analyst"
        )
        
        # Get complete response
        response = await result.get_response()
        tokens = result.metrics['total_tokens']
        duration = result.metrics.get('duration', 0)
        
        return {"analysis": response, "tokens": tokens, "duration": duration}
```

### Streaming Example

```python
async def generate_story(self, topic: str):
    model = await OpenAILanguageModelAction.get(self.model_action_id)
    
    # Streaming query
    result = await model.query_stream(
        prompt=f"Write a story about: {topic}",
        temperature=0.8
    )
    
    # Stream chunks
    chunks = []
    async for chunk in result.iter_stream():
        chunks.append(chunk)
        # Process chunk in real-time
    
    return "".join(chunks)
```

### Using Templates

```python
async def query_with_context(self, query: str, context: str):
    model = await OpenAILanguageModelAction.get(self.model_action_id)
    
    # Apply template
    prompt = await model.apply_template(
        "contextual_query",
        query=query,
        context=context
    )
    
    result = await model.query_sync(prompt)
    return await result.get_response()
```

## API Usage

**Note**: The `model` parameter can be passed in the request body to override the action's default model for a single query.

### Synchronous Query

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

### Streaming Query

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
data: {"delta": " a", "metrics": null, "finish_reason": null}
...
data: {"delta": "", "metrics": {"prompt_tokens": 10, "completion_tokens": 200, "total_tokens": 210, "duration": 2.456}, "finish_reason": "stop", "tool_calls": []}
data: [DONE]
```

### Get Metrics

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

## Templates

Place Jinja2 templates in the `templates/` directory:

### templates/contextual_query.j2
```jinja
{% if context %}
Context:
{{ context }}

{% endif %}Query: {{ query }}
```

### List Templates

```bash
GET /actions/{action_id}/templates
```

### Render Template

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

## Environment Variables

Set your OpenAI API key:

```bash
export OPENAI_API_KEY="sk-..."
```

Or in `.env`:
```
OPENAI_API_KEY=sk-...
```

## Models

Supported models:
- `gpt-4o` - Latest GPT-4 optimized
- `gpt-4o-mini` - Fast and cost-effective (default)
- `gpt-3.5-turbo` - Older model (still supported)
- `gpt-4` - Previous GPT-4
- `gpt-4-turbo` - Fast GPT-4

## Cost Estimation

Approximate pricing (per 1M tokens):

| Model | Input | Output |
|-------|-------|--------|
| gpt-4o | $2.50 | $10.00 |
| gpt-4o-mini | $0.15 | $0.60 |
| gpt-3.5-turbo | $0.50 | $1.50 |

Costs are automatically tracked in the `total_cost` attribute.

