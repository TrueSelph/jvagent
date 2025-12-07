# Persona Action

The Persona Action is a simplified tool-based action for applying agent prompts with configurable parameters.

## Overview

This action provides:
- **Prompt Composition**: Applies the main agent prompt template with persona attributes
- **Configurable Parameters**: Behavioral parameters that are included in the prompt
- **Model Integration**: Uses LanguageModelAction (e.g., OpenAILanguageModelAction) for LLM queries
- **Simple Interface**: Single `respond()` method that takes an Interaction and returns a response

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
      model_action_type: "OpenAILanguageModelAction"  # Entity type to find dynamically
      model_name: "gpt-4o-mini"
      model_temperature: 0.3
      model_max_tokens: 4096
      
      # Optional: Custom prompt template (uses default if not provided)
      # prompt: "Your custom prompt template here. Use {parameters} and {directives} as placeholders."
      
      # Optional: Custom parameters (can also be set in the action class)
      # parameters:
      #   - condition: "User asks about pricing"
      #     response: "Provide pricing information from the pricing directive"
```

## Attributes

- **prompt**: Main agent prompt template (optional, uses default template if not provided)
- **parameters**: List of parameter dictionaries with `condition` and `response` keys
- **model_action_type**: Entity type of the LanguageModelAction to use (e.g., "OpenAILanguageModelAction")
- **model_name**: Default model name for LLM queries
- **model_temperature**: Temperature for LLM generation
- **model_max_tokens**: Max tokens for LLM generation
- **persona_name**: Agent display name (for prompt formatting)
- **persona_role**: Agent role description (for prompt formatting)
- **persona_description**: Detailed agent description (for prompt formatting)
- **persona_capabilities**: List of agent capabilities (for prompt formatting)

## Usage

PersonaAction is a tool-based action that is typically called by InteractActions via the InteractWalker. The main entry point is the `respond()` method:

```python
from jvagent.action.persona import PersonaAction
from jvagent.memory import Interaction

# Get the action
action = await PersonaAction.get(action_id)

# Get or create an interaction
interaction = await conversation.create_interaction(
    utterance="Hello, how can you help me?",
    channel="default"
)

# Generate a response
response = await action.respond(interaction)

print(f"Response: {response}")
```

## Interact Endpoint

PersonaAction is typically used through the InteractWalker, which provides the common entry point for agent interactions:

```http
POST /api/agents/{agent_id}/interact
```

**Request Body:**
```json
{
  "utterance": "Hello, how can you help me?",
  "user_id": "optional_user_id",
  "session_id": "optional_session_id",
  "channel": "default",
  "data": {}
}
```

**Response:**
```json
{
  "user_id": "usr_abc123",
  "session_id": "sess_xyz789",
  "response": "Hello! I'm here to help you...",
  "interaction": {
    "id": "int_123",
    "utterance": "Hello, how can you help me?",
    "response": "Hello! I'm here to help you...",
    "actions": ["PersonaAction", "OpenAILanguageModelAction"],
    "directives": [],
    "parameters": [],
    "model_log": [{"prompt": "...", "system": "...", "response": "...", "metrics": {"total_tokens": 100, "duration": 1.5}}]
  },
  "report": [...]
}
```

## Parameters

Parameters define conditional behaviors that are included in the prompt:

```json
{
  "condition": "User asks about pricing",
  "response": "Provide pricing information from the pricing directive"
}
```

- **condition**: When this parameter applies (descriptive text)
- **response**: Behavioral instruction for the response (included in prompt)

Parameters can be:
- Configured in the action class as the `parameters` attribute
- Set in `agent.yaml` under the action's context
- Added dynamically to the Interaction object by other actions

## Prompt Composition

The system prompt is composed from:
1. The main `prompt` attribute (or default template if not provided)
2. Persona attributes (name, role, description, capabilities)
3. Parameters from `self.parameters` and `interaction.parameters`
4. Directives from `interaction.directives`

The default template includes placeholders for `{parameters}` and `{directives}` that are automatically filled in.

## Example Implementation

```python
from jvagent.action.persona.base import PersonaAction
from jvspatial.core.annotations import attribute
from typing import List, Dict, Any

class MyPersonaAction(PersonaAction):
    # Override persona defaults
    persona_name: str = attribute(
        default="My Assistant",
        description="Agent display name",
    )
    
    # Custom parameters
    parameters: List[Dict[str, Any]] = attribute(
        default_factory=lambda: [
            {
                "condition": "User asks about X",
                "response": "Provide information about X",
            },
        ],
        description="Standard collection of configurable parameters",
    )
```
