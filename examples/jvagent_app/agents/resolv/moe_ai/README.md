# MOE AI Agent

A production-ready switchboard agent for the Ministry of Education, managing and routing users to their subscribed agents (SERT AI or SILDSL AI) through the Resolv Incident Management System.

## Overview

The MOE AI Agent provides:

- Multi-agent routing and switchboard functionality
- Intent-based routing with conversation history management
- Multi-channel support (web and WhatsApp)
- First-time user introduction
- Speech-to-text and text-to-speech capabilities
- Seamless transitions between project-specific agents

## Agent Purpose

The MOE AI serves as the central switchboard for the Ministry of Education's Resolv IMS System, ensuring:

- Smooth transitions between SERT AI and SILDSL AI agents
- Consistent communication across projects
- Seamless access to project-specific support and information
- User onboarding and agent selection guidance

## Structure

```
moe_ai/
├── agent.yaml           # Agent configuration and action assignments
└── README.md           # This file
```

Note: This agent does not have custom actions. It uses core jvagent actions for switchboard functionality.

## Configuration

### Agent Configuration (agent.yaml)

The `agent.yaml` file contains both agent configuration and action assignments:

```yaml
agent: resolv/moe_ai
version: 1.0.0
author: V75 Inc.
jvagent: ~0.0.1

context:
  alias: MOE AI
  description: The MOE AI is the virtual assistant for the Resolv IMS System, managing and routing users to their subscribed agents
  enabled: true

actions:
  # Core routing and model actions
  - action: jvagent/response_gating
  - action: jvagent/interact_router
  - action: jvagent/openai_embedding
  - action: jvagent/intro_interact_action
  - action: jvagent/openai_lm

  # Switchboard actions
  - action: jvagent/switchboard_interact_action
  - action: jvagent/switchboard_interview_interact_action

  # Persona and utilities
  - action: jvagent/persona
  - action: jvagent/agent_utils

  # Integration actions
  - action: jvagent/whatsapp_action
  - action: jvagent/tts_action
  - action: jvagent/stt_action
```

## Actions

### Core Actions

#### InteractRouter

Intent-based routing action that analyzes utterances and routes to appropriate InteractActions.

**Configuration:**

- Model: `gpt-4.1-mini`
- History limit: 3 interactions
- Analyzes conversation context for intelligent routing

#### OpenAI Language Model

Provides LLM integration with GPT-4.1-mini for natural language processing.

**Configuration:**

- Model: `gpt-4.1-mini`
- Temperature: 0.2
- Max tokens: 4096
- Vision support enabled

#### OpenAI Embedding

Generates vector embeddings for semantic search and context retrieval.

**Configuration:**

- Model: `text-embedding-3-small`
- Dimensions: 1536
- Timeout: 30 seconds

#### Intro InteractAction

Introductory action for welcoming first-time users.

**Features:**

- Detects first-time users automatically
- Provides welcome message and guidance
- One-time execution per user

### Switchboard Actions

#### Switchboard InteractAction

Agent routing action that presents available agents and routes users to their selected agent.

**Features:**

- Automatic agent discovery
- Dynamic agent list presentation
- Sub-walker routing to target agents
- State persistence in conversation context

**Configuration:**

- Weight: -10 (runs early for routing)
- Always execute: true

#### Switchboard Interview InteractAction

Structured interview action for guiding users through agent selection.

**Features:**

- Guided selection flow
- Dynamic agent list via context provider
- Input validation
- Cancellation handling
- Completion handling with agent storage

**Configuration:**

- Weight: -20 (runs before switchboard interact)
- Model: `gpt-4.1`
- History limit: 3 interactions
- Auto-confirm: true

### Persona Action

Conversational agent with switchboard-specific personality and capabilities.

**Configuration:**

- Persona name: "Navi"
- Model: `gpt-4.1-mini`
- Temperature: 0.1
- Max tokens: 8192

**Persona Description:**
Friendly and knowledgeable assistant that helps users understand how the resolv framework works. Provides clear, concise answers and demonstrates best practices.

**Capabilities:**

- Can onboard users
- Routing users to their subscribed agents

### Integration Actions

#### WhatsApp Action

Multi-provider WhatsApp integration for messaging.

**Configuration:**

- Provider: `wwebjs` (supports wppconnect, ultramsg, wwebjs)
- Session management
- Media file handling
- Webhook support

**Features:**

- Send and receive messages
- Media file upload/download
- Session persistence
- Multi-provider support

#### Text-to-Speech Action

Converts text responses to speech audio.

**Configuration:**

- Provider: `elevenlabs`
- Model: `eleven_turbo_v2`
- Voice: "Sarah"

#### Speech-to-Text Action

Converts audio messages to text.

**Configuration:**

- Provider: `deepgram`
- Model: `nova-2`

#### Agent Utils Action

Power user controls for agent management and debugging.

**Features:**

- Agent status monitoring
- Configuration inspection
- Debug utilities

## Usage

### Starting the Agent

When jvagent starts from the app directory:

1. The agent configuration is loaded from `agent.yaml` and environment variables are resolved
2. An Agent node is created/updated in the graph
3. Core jvagent actions are loaded from the library
4. Actions are registered with their configuration from the `actions` section in `agent.yaml`
5. The agent is ready to process requests and route to other agents

### Interacting with the Agent

**Web Interface:**

```http
POST /api/agents/{agent_id}/interact
Content-Type: application/json

{
  "utterance": "I want to talk to SERT AI",
  "user_id": null,
  "session_id": null,
  "channel": "default",
  "data": {}
}
```

**WhatsApp:**
Send a message to the configured WhatsApp number. The agent will automatically present available agents and route based on selection.

### Example Workflows

#### First-Time User

1. User: "Hello"
2. Agent presents introduction (via intro_interact_action)
3. Agent presents available agents (SERT AI, SILDSL AI)
4. User selects an agent
5. Agent routes to selected agent

#### Agent Selection

1. User: "I want to switch to SERT AI"
2. Agent routes to `switchboard_interview_interact_action`
3. Agent presents available agents
4. User: "SERT AI"
5. Agent stores selection in conversation context
6. Agent confirms connection to SERT AI
7. Next interaction routes to SERT AI

#### Agent Switching

1. User: "Switch me to SILDSL AI"
2. Agent routes to `switchboard_interview_interact_action`
3. Agent presents available agents
4. User: "SILDSL AI"
5. Agent updates conversation context
6. Agent confirms connection to SILDSL AI
7. Next interaction routes to SILDSL AI

#### Cancellation

1. User: "I want to switch agents"
2. Agent presents available agents
3. User: "Never mind, cancel"
4. Agent clears agent selection
5. Agent confirms user is not connected to any agent

## Environment Variables

This agent uses environment variables for configuration:

**Required:**

- `${OPENAI_API_KEY}` - OpenAI API key for LLM and embeddings
- `${APP_BASE_URL}` - Base URL for the application

**WhatsApp Integration:**

- `${WHATSAPP_API_URL}` - WhatsApp provider API URL
- `${WHATSAPP_API_KEY}` - WhatsApp provider API key
- `${WHATSAPP_SESSION}` - WhatsApp session identifier
- `${WHATSAPP_TOKEN}` - WhatsApp webhook token

**Speech Services:**

- `${TTS_API_KEY}` - Text-to-speech API key (ElevenLabs)
- `${STT_API_KEY}` - Speech-to-text API key (Deepgram)

See the main [jvagent README](../../../../../README.md) for more information about environment variable resolution.

## Switchboard Architecture

### Agent Discovery

The switchboard automatically discovers available agents:

1. Fetches `Agents` node from graph
2. Retrieves all connected agents
3. Filters out current agent (MOE AI)
4. Extracts id, name, alias, and description
5. Caches agent list for performance

### Routing Flow

```
1. User sends message
   ↓
2. InteractRouter processes interaction
   ↓
3. Check: Is agent selected in context?
   ↓ YES: Route to selected agent via sub-walker
   ↓ NO: Present agent list
4. User selects agent
   ↓
5. SwitchboardInterviewInteractAction stores selection
   ↓
6. Next interaction routes to selected agent
```

### Sub-Walker Spawning

When routing to a target agent:

1. Copies visitor data and adds `switchboard_agent_id`
2. Creates new `InteractWalker` with:
   - Target agent ID
   - Original utterance
   - Original channel
   - Copied data with agent context
   - Same session, user, and stream
3. Spawns walker on target agent
4. Target agent processes interaction independently

### Conversation Context

The switchboard uses `conversation.context["switchboard_agent"]` to store:

```python
{
    "id": "agent_uuid",
    "name": "sert_ai",
    "alias": "SERT AI",
    "description": "Virtual assistant for SERT Project"
}
```

## Customization

### Modifying Persona

Update the persona description in `agent.yaml` to customize the agent's behavior:

```yaml
- action: jvagent/persona
  context:
    persona_name: "Navi"
    persona_description: |
      Your custom persona description here
    persona_capabilities:
      - "Custom capability 1"
      - "Custom capability 2"
```

### Adjusting Routing Weights

Action weights control execution order:

- Negative weights run earlier (e.g., -20, -10)
- Positive weights run later (e.g., 100 for fallback)
- Default weight is 0

Update weights in `agent.yaml` under each action's `context.weight`.

### Adding Available Agents

Agents are automatically discovered from the graph. To manually configure:

```yaml
- action: jvagent/switchboard_interact_action
  context:
    switchboard_agents:
      - id: "agent-uuid-1"
        name: "sert_ai"
        alias: "SERT AI"
        description: "Virtual assistant for SERT Project"
      - id: "agent-uuid-2"
        name: "sildsl_ai"
        alias: "SILDSL AI"
        description: "Virtual assistant for SILDSL Project"
```

## Testing

### Switchboard Testing

- Test agent list presentation
- Test agent selection flow
- Test agent switching
- Test cancellation handling
- Verify sub-walker spawning
- Test conversation context persistence

### WhatsApp Integration Testing

- Send messages and verify agent routing
- Test media file handling
- Verify proper response formatting
- Test error handling and fallback responses

### First-Time User Testing

- Test introduction message
- Verify one-time execution
- Test agent selection after introduction

## Known Issues and Fixes

See the [switchboard action READMEs](../../../../../jvagent/action/switchboard_interact_action/README.md) for detailed information about switchboard functionality.

## Future Enhancements

1. **Agent Availability** - Check agent availability before presenting
2. **Load Balancing** - Route to least busy agent automatically
3. **Agent Capabilities** - Match user needs to agent capabilities
4. **Routing Analytics** - Track routing patterns and success rates
5. **Multi-Agent Collaboration** - Support multiple agents working together
6. **Agent Recommendations** - AI-powered agent suggestions based on user query
7. **Fallback Routing** - Automatic fallback if selected agent unavailable

## Support

For issues or questions:

- Review the [jvagent documentation](../../../../../README.md)
- Check the [architecture documentation](../../../docs/architecture.md)
- Review [switchboard action documentation](../../../../../jvagent/action/switchboard_interact_action/README.md)
- Review [switchboard interview action documentation](../../../../../jvagent/action/switchboard_interview_interact_action/README.md)
