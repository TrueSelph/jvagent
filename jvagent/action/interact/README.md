# InteractAction API Guide

This guide documents the API for InteractAction and related methods for adding directives, parameters, and generating responses.

## Overview

InteractAction provides a simplified API for:
- Adding directives and parameters to interactions
- Generating responses via PersonaAction
- Managing interaction state efficiently

## Interact Endpoint Response Format

The `/agents/{agent_id}/interact` endpoint response format varies based on the `JVAGENT_ENVIRONMENT` setting:

### Production Mode (`JVAGENT_ENVIRONMENT=production`)

**Minimal payload** - excludes debugging and observability data:

```json
{
  "user_id": "usr_abc123",
  "session_id": "sess_xyz789",
  "response": "Hello! How can I help you today?",
  "interaction": {
    "id": "int_123",
    "utterance": "Hello",
    "response": "Hello! How can I help you today?"
  }
}
```

**Excluded fields:**
- `report` - Walker traversal report
- `interaction.actions` - Executed actions list
- `interaction.directives` - Directives issued
- `interaction.parameters` - Parameters applied
- `interaction.events` - System events
- `interaction.observability_metrics` - Model calls, token counts, etc.
- `interaction.streamed` - Streaming flag

### Development Mode (`JVAGENT_ENVIRONMENT=development`, default)

**Full payload** - includes all debugging and observability data:

```json
{
  "user_id": "usr_abc123",
  "session_id": "sess_xyz789",
  "response": "Hello! How can I help you today?",
  "interaction": {
    "id": "int_123",
    "utterance": "Hello",
    "response": "Hello! How can I help you today?",
    "actions": ["InteractRouter", "ConverseInteractAction"],
    "directives": [],
    "parameters": [],
    "events": [],
    "observability_metrics": [
      {
        "event_type": "model_call",
        "data": {
          "model": "gpt-4",
          "tokens": 150,
          "duration_ms": 234
        }
      }
    ],
    "streamed": false
  },
  "report": [
    {
      "interaction_created": {
        "interaction_id": "int_123",
        "user_id": "usr_abc123",
        "session_id": "sess_xyz789"
      }
    }
  ]
}
```

### Streaming Responses

For streaming responses (`stream=true`), stream chunk messages also respect the environment mode:

- **Observability data**: Never included in stream chunks (keeps payloads lightweight)
- **Timestamp**: Omitted for `stream_chunk` messages (not useful - chunks arrive in order)
- **Delivered status**: Omitted (only meaningful for channel adapters, not SSE streaming)
- **Final chunk**: Uses the same filtering as non-streaming responses based on `JVAGENT_ENVIRONMENT`

### Configuration

Set the environment variable in your `.env` file or deployment configuration:

```bash
# Development (default) - includes all debug data
JVAGENT_ENVIRONMENT=development

# Production - minimal payload only
JVAGENT_ENVIRONMENT=production
```

**Note:** The environment variable is case-insensitive. Defaults to `development` if not set.

## respond() Method

The `respond()` method is the primary way to generate responses via PersonaAction. It supports passing directives and parameters directly, eliminating the need for separate method calls.

### Signature

```python
async def respond(
    self,
    visitor: "InteractWalker",
    directives: Optional[List[str]] = None,
    parameters: Optional[List[Dict[str, Any]]] = None,
    *,
    # History configuration
    use_utterance: bool = True,
    use_history: bool = True,
    history_limit: int = 3,
    with_interpretation: bool = False,
    with_event: bool = False,
    with_response: bool = True,
    max_statement_length: Optional[int] = None
    
) -> Optional[str]
```

### Parameters

#### History Configuration
- `use_utterance`: Include user utterance in prompt (default: True)
- `use_history`: Include conversation history (default: True)
- `history_limit`: Number of past interactions to include (default: 3)
- `with_interpretation`: Include interpretations in history (default: False)
- `with_event`: Include events in history (default: False)
- `with_response`: Include AI responses in history (default: True)
- `max_statement_length`: Truncate utterances/responses to this length (default: None)

#### Simplified API Parameters
- `directives`: Optional list of directive strings to add before generating response
- `parameters`: Optional list of parameter dictionaries (each should have 'condition' and 'response' keys)

### Examples

#### Basic Usage

```python
# Simple response generation
response = await self.respond(visitor)
```

#### With Directives

```python
# Add directive and generate response in one call
response = await self.respond(
    visitor,
    directives=["Use the provided context to answer the question"]
)
```

#### With Parameters

```python
# Add parameters and generate response
response = await self.respond(
    visitor,
    parameters=[{
        "condition": "No relevant context found",
        "response": "Inform the user that no relevant information was found"
    }]
)
```

#### With Both Directives and Parameters

```python
# Add both directives and parameters
response = await self.respond(
    visitor,
    directives=["Use the provided context to answer"],
    parameters=[{
        "condition": "No relevant context found",
        "response": "Inform the user that no relevant information was found"
    }]
)
```

#### With Conversation History

```python
# Include conversation history
response = await self.respond(
    visitor,
    use_history=True,
    history_limit=5,
    directives=["Answer based on the conversation history"]
)
```

#### Complete Example

```python
response = await self.respond(
    visitor,
    use_history=True,
    history_limit=10,
    with_interpretation=True,
    with_event=True,
    directives=[
        "Use the provided context to answer the question",
        "Be concise and accurate"
    ],
    parameters=[{
        "condition": "No relevant context found",
        "response": "Inform the user that no relevant information was found"
    }]
)
```

## Bulk Methods

For adding multiple directives or parameters efficiently, use the bulk methods on the InteractWalker:

### add_directives()

Add multiple directives with a single save operation:

```python
await visitor.add_directives([
    "Directive 1",
    "Directive 2",
    "Directive 3"
])
```

### add_parameters()

Add multiple parameters with a single save operation:

```python
await visitor.add_parameters([
    {
        "condition": "Condition 1",
        "response": "Response 1"
    },
    {
        "condition": "Condition 2",
        "response": "Response 2"
    }
])
```

## Single-Item Methods

For adding single items, use these convenience methods (they delegate to bulk methods internally):

### add_directive()

```python
await visitor.add_directive("Single directive")
```

### add_parameter()

```python
await visitor.add_parameter({
    "condition": "Some condition",
    "response": "Some response"
})
```

## Best Practices

### 1. Use respond() for Simplified API

**Preferred:**
```python
await self.respond(
    visitor,
    directives=[directive],
    parameters=self.parameters if self.parameters else None
)
```

**Avoid:**
```python
await visitor.add_directive(directive)
if self.parameters:
    for param in self.parameters:
        await visitor.add_parameter(param)
await self.respond(visitor)
```

### 2. Use Bulk Methods for Multiple Items

**Preferred:**
```python
await visitor.add_directives([directive1, directive2, directive3])
```

**Avoid:**
```python
await visitor.add_directive(directive1)
await visitor.add_directive(directive2)
await visitor.add_directive(directive3)
```

### 3. Pass Parameters Correctly

**Correct:**
```python
# self.parameters is already a List[Dict[str, Any]]
await self.respond(visitor, parameters=self.parameters if self.parameters else None)
```

**Incorrect:**
```python
# Don't wrap in another list!
await self.respond(visitor, parameters=[self.parameters])  # ❌ Creates nested list
```

## Benefits

1. **Simplified API**: Fewer method calls, cleaner code
2. **Automatic Persistence**: Interaction is automatically saved
3. **Efficient**: Bulk operations use single save operations
4. **Type Safe**: Proper type hints for all parameters

## See Also

- [InteractAction Base Class](../interact/base.py)
- [InteractWalker](../interact/interact_walker.py)
- [IntroInteractAction README](../intro/README.md)
- [RetrievalInteractAction README](../retrieval/README.md)

## Routing Hints and Exceptions

InteractActions participate in routing via **anchors** and an optional
**always_execute** flag:

- `anchors: List[str]`  
  Describe when the action should be used. `InteractRouter` collects these and
  uses them in its LLM prompt.

- `always_execute: bool`  
  If `True`, the action is treated as a **routing exception** and is always
  allowed to execute, even if it is not explicitly selected by routing.  
  `InteractRouter` will automatically include the class name of such actions in
  `interaction.anchors`, so `InteractWalker` will not skip them.

Examples:

- `IntroInteractAction` sets `always_execute=True` to ensure first‑time user
  handling can run regardless of routing.
- `ConverseInteractAction` sets `always_execute=True` so it can act as a
  last‑resort smalltalk fallback when no other action has produced a response.

See the [InteractRouter README](../router/README.md) for details on how
dynamic routing exceptions are computed.
