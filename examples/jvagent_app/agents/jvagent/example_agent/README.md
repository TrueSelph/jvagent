# Example Agent

This is a boilerplate agent configuration demonstrating how to define a custom agent in jvagent.

## Overview

The Example Agent demonstrates:
- Agent metadata and configuration
- Action assignments
- Agent-specific settings
- Persona-based interactions with LLM-driven parameters

## Structure

```
example_agent/
├── actions/        # Actions packaged with this agent
│   └── jvagent/   # Namespace directory
│       ├── example_action/  # Example action package
│       │   ├── __init__.py
│       │   ├── example_action.py
│       │   ├── endpoints.py
│       │   ├── info.yaml
│       │   ├── requirements.txt
│       │   └── README.md
│       ├── openai_lm/    # OpenAI language model action
│       │   ├── __init__.py
│       │   ├── openai_lm.py
│       │   ├── info.yaml
│       │   ├── requirements.txt
│       │   └── README.md
│       ├── openai_embedding/  # OpenAI embedding model action
│       │   ├── __init__.py
│       │   ├── openai_embedding.py
│       │   ├── info.yaml
│       │   └── README.md
│       └── persona/         # Persona interact action
│           ├── __init__.py
│           ├── persona.py
│           ├── info.yaml
│           ├── requirements.txt
│           └── README.md
├── agent.yaml      # Agent configuration and action assignments
└── README.md      # This file
```

## Configuration

### Agent Configuration (agent.yaml)

The `agent.yaml` file contains both agent configuration and action assignments:

```yaml
# Agent reference in namespace/agent_name format
agent: jvagent/example_agent

# Agent metadata
version: 1.0.0
author: Your Name

# Agent context: Properties that configure the agent
context:
  alias: Example Agent
  description: An example agent demonstrating jvagent agent configuration
  enabled: true

# Action Assignments
actions:
  # Model action for LLM queries
  - action: jvagent/openai_lm
    context:
      enabled: true
      api_key: ${OPENAI_API_KEY}
      model: gpt-4o-mini
  
  # Persona action for interactive conversations
  - action: jvagent/persona
    context:
      enabled: true
      persona_name: "Example Assistant"
      model_action_type: "OpenAILanguageModelAction"
      model: gpt-4o-mini
      model_temperature: 0.3
      model_max_tokens: 4096
  
  # Example action for demonstrations
  - action: jvagent/example_action
    context:
      enabled: true
      timeout: 60
```

## Actions

### Model OpenAI Action

Provides LLM integration with OpenAI models:
- Sync and streaming queries
- Multimodal support (vision)
- Token usage tracking

**API Endpoints:**
- `POST /actions/{action_id}/query` - Query the language model
- `GET /actions/{action_id}/metrics` - Get usage metrics
- `GET /actions/{action_id}/templates` - List available templates
- `POST /actions/{action_id}/templates/{template_name}/render` - Render a template

### Embedding OpenAI Action

Provides embedding model integration with OpenAI's Embeddings API:
- Vector embedding generation
- Multiple model support (text-embedding-3-small, text-embedding-3-large, text-embedding-ada-002)
- Auto-dimension detection
- Metrics tracking

**API Endpoints:**
- `POST /actions/{action_id}/embed` - Generate embedding for text
- `POST /actions/{action_id}/embed/batch` - Generate embeddings for multiple texts
- `GET /actions/{action_id}/embedding/metrics` - Get usage metrics

### Persona Action

Simplified tool-based action for applying agent prompts:
- Prompt composition with persona attributes
- Configurable parameters for behavioral instructions
- Model integration via ModelAction
- Automatic ResponseBus publishing for streaming and non-streaming responses
- Simple `respond()` method interface

**Interact Endpoint (via InteractWalker):**
```http
POST /api/agents/{agent_id}/interact
```

**Request:**
```json
{
  "utterance": "Hello!",
  "user_id": null,
  "session_id": null,
  "channel": "default",
  "data": {}
}
```

**Response:**
```json
{
  "user_id": "usr_abc123",
  "session_id": "sess_xyz789",
  "response": "Hello! How can I help you?",
  "interaction": {
    "id": "int_123",
    "utterance": "Hello!",
    "response": "Hello! How can I help you?",
    "actions": ["PersonaAction", "OpenAILanguageModelAction"],
    "directives": [],
    "parameters": [],
    "observability_metrics": [...]
  },
  "report": [...]
}
```

### PageIndex Retrieval Action

Vectorless RAG from indexed documents (no embeddings). Configure ingestion options under the `config` block in `agent.yaml`:

- `node_summary`: Generate node summaries (required for tree_search)
- `node_text`, `doc_description`: Control what content is stored per node
- `max_token_num_each_node`, `summary_token_threshold`: Token limits for PDF/Markdown

See [PageIndex README](../../../../../jvagent/action/pageindex/README.md) for full configuration.

### Example Action

Demonstrates custom action development with:
- Property configuration
- Custom endpoints
- Lifecycle hooks

## Usage

When jvagent starts from the app directory:

1. The agent configuration is loaded from `agent.yaml` and environment variables are resolved
2. An Agent node is created/updated in the graph
3. Actions are discovered from `actions/{namespace}/{action_name}/` subdirectories
4. Each action's `info.yaml` is read and environment variables are resolved
5. Action classes are loaded and `__init__.py` modules are imported for endpoint discovery
6. Actions are registered with their configuration from the `actions` section in `agent.yaml`
7. The agent is ready to process requests

## Customization

To create your own agent:

1. Create a new directory under `agents/{namespace}/your_agent_name/`
2. Create `agent.yaml` with your agent's configuration (use `namespace/agent_name` format)
3. Add actions to the `actions/{namespace}/{action_name}/` subdirectories
4. Update the `actions` section in `agent.yaml` to assign your desired actions (use `namespace/action_name` format)
5. Use environment variable placeholders (e.g., `${VAR_NAME}`) in YAML files for sensitive values
6. Restart jvagent to load the new agent

## Environment Variables

This agent uses environment variables for configuration:
- `${OPENAI_API_KEY}`: OpenAI API key for the model and embedding actions (set in `.env` file)
- `${TYPESENSE_API_KEY}`: Typesense API key for the vector store (set in `.env` file)

See the main [jvagent README](../../../../../../README.md) for more information about environment variable resolution.
