# jvagent Demo App

This is a boilerplate project for creating a jvagent application. It provides a structured foundation for developing agentive applications with custom agents and actions.

## Project Structure

```
jvagent_app/
├── app.yaml              # Application descriptor (metadata & agent list)
├── agents/               # Custom agent packages
│   └── example_agent/   # Example agent package
│       ├── actions/      # Actions packaged with this agent
│       │   └── example_action/  # Example action package
│       ├── agent.yaml    # Agent configuration and action assignments
│       └── README.md    # Agent documentation
├── docs/                 # Application documentation
├── .env                  # Environment configuration
├── .env.example          # Example environment configuration
├── .gitignore           # Git ignore rules
└── README.md            # This file
```

## Quick Start

1. **Copy this boilerplate** to your project directory:
   ```bash
   cp -r jvagent_app my_agent_app
   cd my_agent_app
   ```

2. **Configure the application descriptor**:
   ```bash
   # Edit app.yaml with your application metadata and agent list
   ```

3. **Configure environment variables**:
   ```bash
   cp .env.example .env
   # Edit .env with your configuration
   ```

4. **Add your custom agents** to the `agents/` directory (see [Agents](#agents) below)

5. **Add actions to each agent** by placing them in `agents/{agent_id}/actions/` (see [Actions](#actions) below)

6. **Update app.yaml** to include your agents in the agents list

7. **Run jvagent** from this app directory:
   ```bash
   cd /path/to/jvagent_app
   jvagent
   ```

## Application Descriptor (app.yaml)

The `app.yaml` file is the main application descriptor that defines:
- Application metadata (name, version, description, etc.)
- Application configuration defaults
- List of agents to install in installation order
- Location of each agent package

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
  file_storage_provider: local
  file_storage_root_dir: ./.files
  file_storage_enabled: true

# Agents to Install (list of namespace/agent_name strings)
agents:
  - jvagent/example_agent
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
   - Discovers actions from `actions/{namespace}/{action_name}/` directories
   - Reads `info.yaml` for each action and resolves environment variables
   - Loads and registers actions with their configuration
   - Imports `endpoints.py` modules for endpoint discovery

## Actions

Actions are pluggable components that extend agent functionality. **Actions are now packaged within each agent folder**, allowing agents to be self-contained packages with their own actions.

### Action Structure

Each action package should be placed within its agent's `actions/` subdirectory:

```
agents/
└── {namespace}/
    └── {agent_name}/
        ├── actions/          # Actions packaged with this agent
        │   └── {namespace}/   # Namespace directory
        │       └── {action_name}/
        │           ├── {action_name}.py  # Main action implementation (Action class)
        │           ├── endpoints.py      # API endpoints (standard pattern)
        │           ├── info.yaml        # Action metadata and configuration
        │           ├── requirements.txt  # Python dependencies (optional)
        │           └── README.md        # Action documentation (optional)
        ├── agent.yaml        # Agent configuration and action assignments
        └── README.md         # Agent documentation (optional)
```

### Action Implementation

Your action class should extend `jvagent.action.action.Action`:

```python
from jvagent.action.action import Action

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
    jvagent: ~2.1.0
    # Other action dependencies (by namespace/action_name)
    actions: []
```

## Agents

Agents define the behavior and configuration of individual agent instances. Each agent package is stored in its own subdirectory under `agents/`.

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

## Configuration

### Application Configuration (app.yaml)

The `app.yaml` file includes a `config` section with application-level defaults:
- Server configuration (host, port, title)
- Database configuration defaults
- File storage defaults
- Authentication defaults
- Admin user defaults

**Note**: Configuration in `app.yaml` can use environment variable placeholders (e.g., `${VAR_NAME}`) which are automatically resolved when the app is loaded.

### Environment Configuration (.env)

The `.env` file contains runtime-specific configuration that overrides `app.yaml` defaults. See `.env.example` for available options.

**Key configuration variables:**
- `JVAGENT_HOST`, `JVAGENT_PORT`: Server host and port
- `JVSPATIAL_DB_PATH`: Database path (production will use its own path)
- `JVSPATIAL_FILE_INTERFACE`: File storage provider (local, s3)
- `JVSPATIAL_FILES_ROOT_PATH`: Root path for file storage (production will use its own path)
- `JVSPATIAL_JWT_SECRET`: JWT secret key (MUST be set in production)
- `JVAGENT_ADMIN_PASSWORD`: Admin password (MUST be set in production)

**Note**: Actions are packaged within each agent folder (`agents/{namespace}/{agent_name}/actions/{namespace}/{action_name}/`). Environment variable placeholders like `${VAR_NAME}` in YAML files are automatically resolved from the environment.

## Running the Application

### Using jvagent CLI

```bash
# Navigate to your app directory
cd /path/to/jvagent_app

# Run jvagent (it will automatically detect app.yaml)
jvagent

# Or using Python module
python -m jvagent
```

### Bootstrap Process

When jvagent starts with an app folder:

1. **Load application descriptor** from `app.yaml` and resolve environment variables
2. **Bootstrap the application graph** with App and Agents nodes
3. **Discover agents** from `agents/{namespace}/{agent_name}/` directory structure
4. **For each agent** listed in `app.yaml`:
   - Read `agent.yaml` and resolve environment variables
   - Create/update the Agent node
   - Discover actions from `actions/{namespace}/{action_name}/` directories
   - Read `info.yaml` for each action and resolve environment variables
   - Load action classes and import `endpoints.py` modules
   - Register actions with their configuration from `agent.yaml`
5. **Start the API server** with all discovered endpoints

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

1. Create a new directory under `agents/`:
   ```bash
   mkdir -p agents/my_new_agent
   ```

2. Create `agent.yaml` with agent configuration and action assignments

3. Add actions to `actions/` subdirectory within the agent folder

4. Update `agent.yaml` to assign actions in the `actions` section

5. Restart jvagent to create the agent instance

## Documentation

Additional documentation can be placed in the `docs/` directory:

- `docs/architecture.md`: Application architecture overview
- `docs/actions.md`: Detailed action development guide
- `docs/agents.md`: Detailed agent configuration guide
- `docs/deployment.md`: Deployment instructions

## License

[Specify your license here]

## Support

For issues and questions:
- Check the [jvagent documentation](https://github.com/your-org/jvagent)
- Open an issue on the project repository

