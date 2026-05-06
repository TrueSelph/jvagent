# jvagent Demo App

This is a boilerplate project for creating a jvagent application. It provides a structured foundation for developing agentive applications with custom agents and actions.

To **generate a new app** from the command line (with optional `profiles/`, deploy stubs, and built-in action profiles), use **`jvagent app create`** — see the jvagent repo’s [docs/scaffolding.md](../../docs/scaffolding.md).

## Project Structure

```
jvagent_app/
├── app.yaml              # Application descriptor (metadata & agent list)
├── agents/               # Custom agent packages
│   ├── jvagent/
│   │   ├── cockpit_agent/   # Cockpit agent (model with full tool agency)
│   │   │   ├── agent.yaml
│   │   │   └── README.md
│   │   ├── example_agent/   # Main demo agent (core + PageIndex, signup interview)
│   │   │   ├── actions/jvagent/signup_interview_interact_action/
│   │   │   ├── agent.yaml
│   │   │   └── README.md
│   │   ├── unified_agent/   # AgentInteractAction (unified routing + skill loop)
│   │   │   ├── agent.yaml
│   │   │   └── README.md
│   │   └── skills_agent/   # Optional Ollama + skills (add to app.yaml to enable)
│   └── resolv/
│       └── resolv_demo/     # Resolv API + interview flows (set RESOLV_TEST_* in .env)
│           ├── actions/resolv/
│           ├── agent.yaml
│           └── README.md
├── docs/                 # Application documentation
├── .env                  # Environment configuration
├── .env.example          # Example environment configuration
├── .gitignore           # Git ignore rules
└── README.md            # This file
```

## Quick Start

### Running the Example Application

After installing jvagent, you can run this example application:

> **PageIndex document retrieval**: The example agent includes `jvagent/pageindex_action`. Install jvagent with the pageindex extra: `pip install jvagent[pageindex]` or `pip install -e ".[pageindex]"` from the jvagent repo. Documents must be ingested via `POST /pageindex/documents` before retrieval works. Ingestion options (`node_summary`, `node_text`, etc.) are configured under the action's `config` block in `agent.yaml`—see [PageIndex README](../jvagent/action/pageindex/README.md).

1. **Navigate to the jvagent repository root** (where you installed jvagent)

2. **Set up environment variables** (if not already done):
   ```bash
   cd examples/jvagent_app
   cp .env.example .env
   # Edit .env and set at minimum:
   # - JVAGENT_ADMIN_PASSWORD (required)
   # - OPENAI_API_KEY (needed for the bundled agents' OpenAI actions)
   # For resolv/resolv_demo only: RESOLV_TEST_* variables (see .env.example)
   cd ../..
   ```

3. **Run the example application**:
   ```bash
   # From the jvagent repository root
   jvagent examples/jvagent_app
   ```

   Or change to the example directory first:
   ```bash
   cd examples/jvagent_app
   jvagent
   ```

4. **Access the API**:
   - API Documentation: http://localhost:8000/docs
   - Server: http://localhost:8000

### Using This as a Template

1. **Copy this boilerplate** to your project directory:
   ```bash
   cp -r examples/jvagent_app /path/to/my_agent_app
   ```

2. **Configure the application descriptor**:
   ```bash
   # Edit app.yaml with your application metadata and agent list
   ```

3. **Configure environment variables**:
   ```bash
   cd /path/to/my_agent_app
   cp .env.example .env
   # Edit .env with your configuration
   ```

4. **Add your custom agents** to the `agents/` directory (see [Agents](#agents) below)

5. **Configure actions** in each agent's `agent.yaml`:
   - Use core actions directly (e.g., `jvagent/interact_router`)
   - Add custom actions in `agents/{namespace}/{agent_name}/actions/` (see [Actions](#actions) below)

6. **Update app.yaml** to include your agents in the agents list

7. **Run jvagent** with your app directory:
   ```bash
   # Recommended: Specify app root path
   jvagent /path/to/my_agent_app

   # Or change to the directory first
   cd /path/to/my_agent_app
   jvagent
   ```

## Application Descriptor (app.yaml)

The `app.yaml` file is the main application descriptor that defines:
- Application metadata (name, version, description, etc.)
- Safe application configuration defaults
- List of agents (agents listed here are automatically installed when you run jvagent)
- Location of each agent package (discovered from the `agents/` directory structure)

### Example app.yaml

```yaml
# Application reference
app: jvagent_demo_app

# Application metadata
version: 1.0.0
author: Your Name/Organization

# Application context: Properties that configure the App node
context:
  name: jvagent Demo App
  description: Demo application

config:
  server:
    title: Demo API
    description: API for demo app
    version: 0.0.1

  auth:
    enabled: true
    exempt_paths:
      - /health
      - /docs
      - /openapi.json

# Agents (list of namespace/agent_name strings)
# Agents listed here are automatically installed when you run jvagent or bootstrap
agents:
  - jvagent/example_agent
  - resolv/resolv_demo
  # Optional: jvagent/skills_agent
```

### How It Works

When jvagent starts from an app directory, it:
1. Reads `app.yaml` to get application configuration
2. Resolves environment variable placeholders (e.g., `${VAR_NAME}`)
3. Creates/updates the App node from `app.yaml` context
4. Discovers agents from the `agents/{namespace}/{agent_name}/` directory structure
5. For each agent listed in `app.yaml`:
   - Reads `agent.yaml` to get agent configuration
   - Resolves environment variables in agent config
   - Creates/updates the Agent node
   - Scans `agent.yaml` to identify required actions
   - Resolves transitive dependencies from `info.yaml` files
   - Discovers actions from `actions/{namespace}/{action_name}/` directories (only for required actions)
   - Reads `info.yaml` for each required action and resolves environment variables
   - Loads action classes conditionally (only for required actions and their dependencies)
   - Imports `endpoints.py` modules for endpoint discovery (only for loaded actions)
   - Registers actions with their configuration from `agent.yaml`
   - **Important**: Actions not listed in any `agent.yaml` remain unloaded and their endpoints are not accessible

## Actions

Actions are pluggable components that extend agent functionality. Actions can be:
1. **Core actions** from the jvagent library (loaded conditionally based on `agent.yaml`)
2. **Local actions** packaged within each agent folder
3. **Local overrides** of core actions (takes precedence over core)

**Conditional Loading**: Actions are only loaded if they are explicitly listed in `agent.yaml` or are required as dependencies of a loaded action. This ensures that unused actions remain unloaded and their endpoints are not accessible.

### Action Discovery and Conditional Loading

Actions are discovered and loaded conditionally based on `agent.yaml` configuration:

1. **Required Actions**: Actions explicitly listed in `agent.yaml` are marked as required
2. **Dependency Resolution**: For each required action, dependencies are resolved transitively from `info.yaml` files
3. **Action Loading**: Only required actions (and their dependencies) are loaded:
   - **Local actions** from `actions/{namespace}/{action_name}/` (takes precedence)
   - **Core actions** from jvagent library (`jvagent/action/*/`) if not found locally
4. **Endpoint Registration**: Endpoints are only registered for loaded actions
5. **Unused Actions**: Actions not listed in any `agent.yaml` remain completely unloaded (no module import, no endpoints)

**Important**: Only actions explicitly listed in `agent.yaml` (or required as dependencies) are loaded. Unused actions remain unloaded and their endpoints are not accessible.

### Using Core Actions

This example app demonstrates using core actions directly from the jvagent library. Core actions like `interact_router`, `openai_lm`, `openai_embedding`, `typesense_vectorstore`, and `retrieval_interact_action` are referenced in `agent.yaml` without requiring stub directories.

### Action Structure

#### Local Actions

Each local action package should be placed within its agent's `actions/` subdirectory:

```
agents/
└── {namespace}/
    └── {agent_name}/
    ├── actions/          # Actions packaged with this agent
        │   └── {namespace}/   # Namespace directory
        │       └── {action_name}/
        │           ├── {action_name}.py  # Main action implementation (Action class)
        │           ├── __init__.py        # Package initialization
        │           ├── endpoints.py      # API endpoints (standard pattern)
        │           ├── info.yaml        # Action metadata and configuration
        │           ├── requirements.txt  # Python dependencies (optional)
        │           └── README.md        # Action documentation (optional)
        ├── agent.yaml        # Agent configuration and action assignments
        └── README.md         # Agent documentation (optional)
```

#### Example App Actions

**`jvagent/example_agent`** (default in `app.yaml`):
- **Core actions** (from the jvagent library):
  - `jvagent/interact_router` — Posture + intent routing
  - `jvagent/openai_lm` — OpenAI language model
  - `jvagent/openai_embedding` — Embeddings
  - `jvagent/intro_interact_action` — First-time user intro
  - `jvagent/pageindex_action` — PageIndex RAG (install `jvagent[pageindex]`)
  - `jvagent/persona` — Persona
  - `jvagent/converse_interact_action` — Smalltalk fallback
- **Local action**: `jvagent/signup_interview_interact_action` (under `agents/jvagent/example_agent/actions/`)

**`resolv/resolv_demo`**: custom `resolv/*` interview and API actions plus `jvagent/access_control_action` and `jvagent/whatsapp_action`; configure `RESOLV_TEST_*` in `.env` for `resolv/resolv_api_action`.

### Using Core Actions

To use a core action, simply reference it in `agent.yaml`:

```yaml
actions:
  - action: jvagent/interact_router
    context:
      enabled: true
      model_action_type: "OpenAILanguageModelAction"
      history_limit: 3
      enable_routing_cache: true  # Optional: skip LLM for repeated context (requires enable_interact_router_cache in app.yaml)
```

No stub directory needed - the action is automatically loaded from the core library when listed in `agent.yaml`.

**Conditional Loading**: Core actions are only loaded if they are explicitly listed in `agent.yaml` or are required as dependencies of a loaded action. This ensures that unused actions remain unloaded and their endpoints are not accessible.

### Action Implementation

For custom actions, your action class should extend `jvagent.action.base.Action`:

```python
from jvagent.action.base import Action

class MyAction(Action):
    """My custom action implementation."""

    async def on_register(self):
        """Called when action is registered."""
        pass

    async def on_enable(self):
        """Called when action is enabled."""
        pass

    # Implement other lifecycle hooks as needed
```

### Action Metadata (info.yaml)

Each action must include an `info.yaml` file:

```yaml
package:
  # Action name in namespace/action_name format
  name: jvagent/my_action

  # Package author
  author: Your Name/Organization

  # Archetype: The main Action class name (same as the Action Node class)
  archetype: MyAction

  # Package version
version: 1.0.0

  # Package metadata
  meta:
    title: My Custom Action
description: A description of what this action does
    group: jvagent
    type: action

  # Package dependencies
dependencies:
    # jvagent version requirement
    jvagent: ~0.0.1
    # Other action dependencies (by namespace/action_name)
  actions: []
```

## Agents

Agents define the behavior and configuration of individual agent instances. Each agent package is stored in its own subdirectory under `agents/`.

**Important**: Agents are installed automatically from `app.yaml` when you run jvagent or bootstrap. There is no direct agent installation - agents must be listed in the `agents` section of `app.yaml`.

### Agent Structure

Each agent package should follow this structure:

```
agents/
└── my_agent/
    ├── agent.yaml        # Agent configuration and action assignments
    └── README.md        # Agent documentation (optional)
```

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
  custom_field: value  # Any additional public properties

# Action Assignments
# Actions are discovered from namespace subdirectories: actions/{namespace}/{action_name}/
# Actions are referenced using the format: namespace/action_name
actions:
  - action: jvagent/example_action
    context:
    enabled: true
      description: "Customized example action for demonstration"
      timeout: 60
      retries: 5
      api_endpoint: "https://prod.api.example.com"
```

`agent.yaml` uses warn-only structural validation:
- expected top-level structure is validated (`agent`, metadata, `context`, `actions`)
- each `actions[]` entry is validated for shape (`action`, optional `context`, optional `config`)
- custom keys inside `actions[].context` and `actions[].config` are allowed for custom actions

## Configuration

### Configuration Priority

Configuration is loaded with the following priority (highest to lowest):
1. **Environment variables** (from `.env` file or system environment) - Highest priority
2. **app.yaml config section** - Default values
3. **Hardcoded defaults** in code - Lowest priority

### Application Configuration (app.yaml)

Use `app.yaml` for app structure and high-convenience defaults that are safe in git:

- `app`, `context` metadata, `agents`
- `config.server` metadata (`title`, `description`, `version`, docs routes)
- `config.auth.enabled` and `config.auth.exempt_paths`
- `config.interact` limits
- `config.cors` defaults
- `config.performance` defaults

Keep these env-first (even when YAML fallbacks exist):

- Secrets (`JVSPATIAL_JWT_SECRET_KEY`, `JVAGENT_ADMIN_PASSWORD`, vendor API keys)
- System/runtime and deploy-specific values (`JVAGENT_HOST`, `JVAGENT_PORT`, DB/storage/log backend keys)
- Credentials (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, S3 keys)

The descriptor follows an expected-key model:

- use only documented top-level keys and documented `config` sections
- any key outside the expected model is flagged as unexpected at startup

### Environment Configuration (.env)

The `.env` file should contain secrets and environment-specific overrides:

**Required Secrets:**
- `OPENAI_API_KEY` - OpenAI API key (for language model actions)
- `TYPESENSE_API_KEY` - Typesense API key (for vector store actions)
- `JVSPATIAL_JWT_SECRET_KEY` - JWT secret key (MUST be set in production)
- `JVAGENT_ADMIN_PASSWORD` - Admin password (MUST be set in production)

**Optional Overrides:**
You can override any `app.yaml` configuration using environment variables if needed for local development:
- `JVAGENT_HOST`, `JVAGENT_PORT` - Override server host/port
- `JVSPATIAL_MONGODB_URI` - Override MongoDB URI
- `JVSPATIAL_MONGODB_DB_NAME` - Override database name
- `JVSPATIAL_LOG_LEVEL` - Override log level (`debug`, `info`, `warning`, `error`; same as jvspatial)

**Important**:
- Add `.env` to `.gitignore` to prevent committing secrets
- In production, use secure secret management (environment variables, secret managers, etc.)
- Put deploy-specific values in env even when YAML fallbacks exist

## Running the Application

### Using jvagent CLI

**Option 1: Specify app root path (recommended)**
```bash
# Run from anywhere, specifying the app directory path
jvagent /path/to/jvagent_app

# With flags
jvagent /path/to/jvagent_app --update --debug

# Fresh start (development mode only) - deletes database and logs
jvagent /path/to/jvagent_app --purge

# Or using Python module
python -m jvagent /path/to/jvagent_app
```

**Option 2: Run from within the app directory**
```bash
# Navigate to your app directory
cd /path/to/jvagent_app

# Run jvagent (uses current directory as app root)
jvagent

# Or using Python module
python -m jvagent
```

**Note:** jvagent automatically detects `app.yaml` in the specified app root directory (or current directory if not specified).

### Bootstrap Process

When jvagent starts with an app directory (either specified as a path or from within the directory):

1. **Load application descriptor** from `app.yaml` in the app root directory and resolve environment variables
2. **Bootstrap the application graph** with App and Agents nodes
3. **Discover agents** from `agents/{namespace}/{agent_name}/` directory structure
4. **For each agent** listed in `app.yaml`:
   - Read `agent.yaml` and resolve environment variables
   - Create/update the Agent node
   - Scan `agent.yaml` to identify required actions
   - Resolve transitive dependencies from `info.yaml` files
   - Discover actions from `actions/{namespace}/{action_name}/` directories (only for required actions)
   - Read `info.yaml` for each required action and resolve environment variables
   - Load action classes conditionally (only for required actions and their dependencies)
   - Import `endpoints.py` modules via `__init__.py` (only for loaded actions)
   - Register actions with their configuration from `agent.yaml`
5. **Start the API server** with endpoints from loaded actions only

## Development

### Adding a New Action

1. Create a new directory under the agent's `actions/` folder:
   ```bash
   mkdir -p agents/my_agent/actions/jvagent/my_new_action
   ```

2. Create the action implementation:
   ```bash
   touch agents/my_agent/actions/jvagent/my_new_action/my_new_action.py
   ```

3. Create the `info.yaml` metadata file

4. **Create `__init__.py` and `endpoints.py`** (standard pattern):
   ```bash
   touch agents/my_agent/actions/jvagent/my_new_action/__init__.py
   touch agents/my_agent/actions/jvagent/my_new_action/endpoints.py
   ```

   In `__init__.py`, import the action class and endpoints:
   ```python
   from .my_new_action import MyNewAction
   from . import endpoints  # noqa: F401
   ```

5. Add any required dependencies to `requirements.txt`

6. Update the agent's `agent.yaml` to assign the new action in the `actions` section

7. Restart jvagent to discover and load the new action

**Standard Action Structure:**
```
my_new_action/
├── __init__.py        # Package initialization (imports action & endpoints)
├── my_new_action.py   # Action class with business logic
├── endpoints.py       # HTTP API endpoints (standard pattern)
├── info.yaml         # Action metadata
├── requirements.txt  # Dependencies
└── README.md         # Documentation
```

### Adding a New Agent

1. Create a new directory under `agents/{namespace}/`:
   ```bash
   mkdir -p agents/jvagent/my_new_agent
   ```

2. Create `agent.yaml` with agent configuration and action assignments (use `namespace/agent_name` format)

3. Add actions to `actions/{namespace}/{action_name}/` subdirectories within the agent folder

4. Update `agent.yaml` to assign actions in the `actions` section

5. **Add the agent to `app.yaml`** in the `agents` list:
   ```yaml
   agents:
     - jvagent/my_new_agent
   ```

6. Run jvagent or bootstrap to install the agent:
   ```bash
   jvagent /path/to/jvagent_app
   # Or
   jvagent /path/to/jvagent_app bootstrap
   ```

**Note**: Agents are only installed via `app.yaml` - there is no direct agent installation command.

## Documentation

Additional documentation can be placed in the `docs/` directory:

- `docs/architecture.md`: Application architecture overview

For action development, agent configuration, and deployment, see the main [jvagent README](../../README.md) and [Documentation Index](../../README.md#documentation-index).

## License

[Specify your license here]

## Support

For issues and questions:
- Check the [jvagent documentation](https://github.com/your-org/jvagent)
- Open an issue on the project repository
