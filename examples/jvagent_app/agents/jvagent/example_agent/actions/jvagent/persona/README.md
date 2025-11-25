# Persona Action

The Persona Action is a core interact action for agent behavioral modeling with LLM-driven parameters.

## Overview

This action provides:
- **LLM-Driven Parameters**: Behavioral parameters that are filtered by an LLM based on conversation context
- **Action Delegation**: Parameters can trigger other actions that contribute directives to the final response
- **Canned Responses**: Quick responses for simple requests before full processing
- **Event Bus**: Asynchronous response handling with streaming support
- **Session Management**: User and conversation tracking with session_id

## Configuration

The action can be configured in `agent.yaml`:

```yaml
actions:
  - action: jvagent/persona
    context:
      enabled: true
      # Persona Identity
      persona_name: "My Assistant"
      persona_role: "A helpful AI assistant"
      persona_description: "You are friendly and knowledgeable..."
      persona_capabilities:
        - "Answer questions"
        - "Process requests"
      
      # Model Configuration
      model_action_id: "jvagent.OpenAIModelAction.xxx"  # ID of ModelAction to use
      model_name: "gpt-4o-mini"
      light_model_name: "gpt-4o-mini"  # For parameter filtering
      model_temperature: 0.3
      model_max_tokens: 4096
      
      # Behavior
      canned_responses_enabled: false
      streaming: false
      history_enabled: true
      history_size: 5
```

## API Endpoints

### Interact

```http
POST /api/actions/{action_id}/interact
```

**Request Body:**
```json
{
  "utterance": "Hello, how can you help me?",
  "user_id": "optional_user_id",
  "session_id": "optional_session_id",
  "channel": "default"
}
```

**Response:**
```json
{
  "user_id": "usr_abc123",
  "session_id": "sess_xyz789",
  "response": "Hello! I'm here to help you...",
  "canned_response": null,
  "interaction": {
    "id": "int_123",
    "utterance": "Hello, how can you help me?",
    "response": "Hello! I'm here to help you...",
    "actions": ["PersonaAction", "OpenAIModelAction"],
    "directives": [],
    "parameters": [{"id": "param_1", "condition": "...", "response": "..."}],
    "model_log": [{"prompt": "...", "system": "...", "response": "...", "metrics": {"total_tokens": 100, "duration": 1.5}}]
  },
  "events": [...]
}
```

### Session Management

- **First message (no IDs)**: Creates new user and conversation, returns both IDs
- **Continue conversation (session_id only)**: Uses existing conversation
- **New conversation (user_id only)**: Creates new conversation for existing user
- **Resume specific (both IDs)**: Validates and uses existing conversation

### Parameters

```http
GET /api/actions/{action_id}/parameters
POST /api/actions/{action_id}/parameters
PUT /api/actions/{action_id}/parameters/{param_id}
DELETE /api/actions/{action_id}/parameters/{param_id}
POST /api/actions/{action_id}/parameters/import
```

## Parameters

Parameters define conditional behaviors:

```json
{
  "id": "param_1",
  "condition": "User asks about pricing",
  "response": "Provide pricing information from the pricing directive",
  "action": "PricingAction",
  "enabled": true
}
```

- **condition**: When this parameter applies (evaluated by LLM)
- **response**: Behavioral instruction for the response
- **action**: Optional action label to trigger (calls `execute()`)
- **enabled**: Whether this parameter is active

## Event Bus

The interaction emits events throughout processing:

- `interaction_started` - Processing begins
- `canned_response` - Quick response sent
- `parameter_filtered` - Parameters selected by LLM
- `action_triggered` - Helper action called
- `action_result` - Directive from action
- `response_chunk` - Streaming chunk
- `response_complete` - Final response
- `interaction_complete` - Processing finished

## Example Usage

```python
from jvagent.action.persona import PersonaAction

# Get the action
action = await PersonaAction.get(action_id)

# Process an interaction
result = await action.interact(
    utterance="Hello!",
    user_id=None,  # Auto-generate
    session_id=None,  # Auto-generate
    channel="web"
)

print(f"User: {result.user_id}")
print(f"Session: {result.session_id}")
print(f"Response: {result.response}")
```

