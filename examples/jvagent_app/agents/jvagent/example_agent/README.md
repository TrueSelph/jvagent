# Example Agent

This is a boilerplate agent configuration demonstrating how to define a custom agent in jvagent.

## Overview

The Example Agent demonstrates:
- Agent metadata and configuration
- Action assignments
- Agent-specific settings

## Structure

```
example_agent/
├── actions/        # Actions packaged with this agent
│   └── jvagent/   # Namespace directory
│       ├── example_action/  # Example action package
│       │   ├── example_action.py
│       │   ├── endpoints.py
│       │   ├── info.yaml
│       │   ├── requirements.txt
│       │   └── README.md
│       └── model_openai/    # OpenAI model action
│           ├── model_openai.py
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
# Actions are discovered from namespace subdirectories: actions/{namespace}/{action_name}/
# Actions are referenced using the format: namespace/action_name
actions:
  - action: jvagent/example_action
    context:
      enabled: true
      description: "Example action for demonstration"
      timeout: 60
      retries: 5
  
  - action: jvagent/model_openai
    context:
      enabled: true
      api_key: ${OPENAI_API_KEY}
      model: gpt-4o
```

## Usage

When jvagent starts from the app directory:

1. The agent configuration is loaded from `agent.yaml` and environment variables are resolved
2. An Agent node is created/updated in the graph
3. Actions are discovered from `actions/{namespace}/{action_name}/` subdirectories
4. Each action's `info.yaml` is read and environment variables are resolved
5. Action classes are loaded and `endpoints.py` modules are imported for endpoint discovery
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
- `${OPENAI_API_KEY}`: OpenAI API key for the model action (set in `.env` file)

See the main [jvagent README](../../../../../../README.md) for more information about environment variable resolution.

