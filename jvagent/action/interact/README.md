# InteractAction API Guide

This guide documents the API for InteractAction and related methods for adding directives, parameters, and generating responses.

## Overview

InteractAction provides a simplified API for:
- Adding directives and parameters to interactions
- Generating responses via PersonaAction
- Managing interaction state efficiently

## Architecture

### Modular Pipeline Design

InteractActions serve as **modular points of execution** that may exist in a prescribed chain of interact actions. The InteractWalker traverses and executes this modular pipeline of interact actions.

### Walker Traversal

The InteractWalker is designed to traverse and execute the modular pipeline of interact actions. Core actions like InteractRouter, when employed, provide additional logic which alters or curates the walker's walk path or traversal, allowing specific actions to be executed based on the nature of the input.

### Top-Level Action Routing

While interact actions may have branches of other interact actions, **top-level interact actions** (that is, the actions directly connected to the Actions branch node) **must employ logic to further route the interact walker to its children** (since this may always be done conditionally) instead of having it done automatically.

This means that if your top-level InteractAction has child InteractActions connected to it, you must explicitly route the walker to those children within your `execute()` method:

```python
class MyTopLevelAction(InteractAction):
    async def execute(self, visitor: InteractWalker) -> None:
        # Perform action logic
        # ...

        # Explicitly route to child actions conditionally
        if some_condition:
            child_action = await self.node(node="ChildInteractAction")
            if child_action:
                await visitor.visit(child_action)
```

**Important:** The walker will NOT automatically traverse child InteractActions from top-level actions. This design allows for conditional routing based on the action's internal logic and state.

## Interact Endpoint Response Format

The `/agents/{agent_id}/interact` endpoint response format varies based on environment mode. Use **production** mode for shorter, more secure payloads (minimal fields only). Configuration:

- **`JVSPATIAL_ENVIRONMENT`** env var

### Production Mode (shorter, secure payloads)

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
- `interaction.tasks` - Consolidated task list (each entry carries status: active/completed/failed/cancelled)
- `interaction.observability_metrics` - Model calls, token counts, etc.
- `interaction.streamed` - Streaming flag

### Development Mode (`JVSPATIAL_ENVIRONMENT=development`, default)

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
    "tasks": [
      {
        "id": "task_001",
        "title": "Hello",
        "status": "completed",
        "steps": []
      }
    ],
    "parameters": [],
    "events": [],
    "observability_metrics": [
      {
        "event_type": "model_call",
        "data": {
          "model": "gpt-4",
          "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "total_tokens": 150
          },
          "duration": 0.234
        }
      }
    ],
    "usage": {
      "prompt_tokens": 100,
      "completion_tokens": 50,
      "total_tokens": 150,
      "model_call_count": 1,
      "estimated_cost_usd": 0.0001,
      "total_duration_seconds": 0.234,
      "last_updated": "2025-01-02T12:00:00Z"
    },
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
- **Final chunk**: Uses the same filtering as non-streaming responses based on `JVSPATIAL_ENVIRONMENT`

### Configuration

For shorter, secure interact payloads in production, set environment mode to `production`:

**Option 1: Environment variable** (`.env` or deployment config)
```bash
# Development (default) - includes all debug data
JVSPATIAL_ENVIRONMENT=development

# Production - minimal payload only (recommended for production)
JVSPATIAL_ENVIRONMENT=production
```

The environment variable is case-insensitive. Defaults to `development` if unset.

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

## Visitor Data and Image Support

**Standard key**: `visitor.data["image_urls"]` is the canonical key for image input across channels (WhatsApp, Interact API, etc.). Media sources should populate this key with:

- A list of URL strings
- A list of dicts: `{"url": "..."}` or `{"base64": "..."}` (with optional `detail` for vision models)

When `image_urls` is populated, PersonaAction uses `build_prompt_for_vision()` (from `jvagent.action.interact.utils.vision_prompt`) to build multimodal prompts for vision-capable LLMs. The helper accepts `image_data_keys` (default: `("image_urls",)`) so channels can use a single standard key.

**Image interpretation memory**: `generate_image_interpretation()` produces an extensive image description behind the scenes. PersonaAction stores it on `interaction.image_interpretation` and injects it into the system prompt for follow-up questions when the current request has no images. Call only when `visitor.data.get("image_interpretation")` is not `False`.

**Suppression**: Set `visitor.data["image_interpretation"] = False` to skip vision (e.g. when images are document uploads for an interview). The interview action sets this automatically when media is submitted via `data_input_field`. When suppressed: no images are passed to the model, no interpretation is generated, and no interpretation is stored.

**Related keys**:
- `whatsapp_media`: All media URLs (images, documents, video, audio). Used by interview actions with `data_input_field: "whatsapp_media"` for document uploads.

## Task Tracking

Actions that manage multi-turn flows requiring user input (e.g., interviews) use
the conversation-scoped `TaskService` exposed as `visitor.tasks`:

```python
handle = await visitor.tasks.start(
    description="Guide user to complete SignupInterviewSkill",
    task_type="INTERVIEW",
    action_name="SignupInterviewSkill",
    metadata={"state": "ACTIVE"},
    singleton_action=True,
)

await handle.complete()

tasks = visitor.tasks.list(status="active")
```

Tasks are stored on the **Conversation** (not per-interaction). In development mode, `interaction.tasks` in the response payload shows the consolidated task list — currently active tasks plus any tasks that reached a terminal status (completed, failed, cancelled) within this interaction's window. Each entry carries its own `status` field.

See [Task Tracking](../../../docs/task-tracking.md) for full documentation.

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

- `jvagent.action.interact.utils.vision_prompt` - `build_prompt_for_vision()` for multimodal prompts from `visitor.data["image_urls"]`
- [WhatsApp Action](../whatsapp/README.md) - Image flow, quoted image reply support
- [InteractAction Base Class](base.py)
- [InteractWalker](interact_walker.py)
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
