# jvagent

A modular, pluggable agentive platform built on jvspatial that provides a production-ready framework for AI agent development with enterprise-grade observability and scalability.

## Table of Contents

- [Installation](#installation)
- [Quick Start](#quick-start)
- [Running jvagent in an App Directory](#running-jvagent-in-an-app-directory)
- [Core Concepts](#core-concepts)
- [Directory Structure](#directory-structure)
- [Configuration Files](#configuration-files)
- [Creating Actions](#creating-actions)
- [Action Lifecycle](#action-lifecycle)
- [Property Configuration](#property-configuration)
- [Namespace System](#namespace-system)
- [API Usage](#api-usage)
- [Development](#development)

## Installation

### Prerequisites

- Python 3.8 or higher
- pip

### Install from Source

1. Clone the repository:
   ```bash
   git clone <repository-url>
   cd jvagent
   ```

2. Install in development mode:
   ```bash
   pip install -e .
   ```

   Or install with development dependencies:
   ```bash
   pip install -e ".[dev]"
   ```

### Install from Distribution

If you have a built distribution:
```bash
pip install dist/jvagent-*.whl
```

## Quick Start

### 1. Configure Environment

Copy the example environment file and update with your values:
```bash
cp .env.example .env
```

Edit `.env` and set at minimum:
- `JVAGENT_ADMIN_PASSWORD` - Password for the initial admin user
- `JVSPATIAL_JWT_SECRET` - Secret key for JWT authentication (change from default in production)

### 2. Run jvagent

After installation, you can run jvagent in several ways:

**Option 1: Using the console command**
```bash
jvagent
```

**Option 2: Using Python module**
```bash
python -m jvagent
```

The server will start on `http://127.0.0.1:8000` by default (configurable via `.env`).

### 3. Access the API

- **API Documentation**: http://localhost:8000/docs
- **Alternative Docs**: http://localhost:8000/redoc

### 4. Login

Use the admin credentials from your `.env` file:
```bash
curl -X POST "http://localhost:8000/auth/login" \
  -H "Content-Type: application/json" \
  -d '{
    "email": "admin@jvagent.example",
    "password": "your-admin-password"
  }'
```

## Running jvagent in an App Directory

jvagent is designed to work with a declarative app directory structure. When you run `jvagent` from a directory containing an `app.yaml` file, it automatically discovers and loads all agents and actions defined in your YAML configuration files.

### Creating a jvagent App Directory

A jvagent app directory should have the following structure:

```
my_jvagent_app/
├── app.yaml                    # Application configuration
├── .env                        # Environment variables
└── agents/
    └── {namespace}/
        └── {agent_name}/
            ├── agent.yaml      # Agent configuration
            └── actions/
                └── {namespace}/
                    └── {action_name}/
                        ├── info.yaml
                        └── {action_name}.py
```

### Running jvagent from an App Directory

1. **Navigate to your app directory:**
   ```bash
   cd /path/to/my_jvagent_app
   ```

2. **Ensure `app.yaml` exists:**
   ```bash
   ls app.yaml  # Should exist
   ```

3. **Run jvagent:**
   ```bash
   jvagent
   ```
   
   Or using Python module:
   ```bash
   python -m jvagent
   ```

### What Happens When You Run jvagent

When jvagent starts in a directory with `app.yaml`, it automatically:

1. **Reads `app.yaml`** to get application configuration
2. **Creates/updates the App node** from `app.yaml` context
3. **Discovers agents** from the `agents/` directory structure
4. **For each agent listed in `app.yaml`:**
   - Reads `agent.yaml` to get agent configuration
   - Creates/updates the Agent node
   - Discovers actions from `actions/{namespace}/{action_name}/` directories
   - Reads `info.yaml` for each action
   - Loads and registers actions with their configuration

### Default Behavior vs. Update Mode

**Default Behavior (No `--update` flag):**
- Uses existing agents and actions from the database
- Only installs new agents/actions that don't exist
- Does **not** overwrite existing agent/action context
- Safe for repeated runs - won't overwrite manual changes

**Update Mode (`--update` or `--migrate` flag):**
- Updates existing agents and actions with values from YAML files
- Overwrites agent/action context with values from `app.yaml` and `agent.yaml`
- Useful when you've updated YAML files and want to apply changes
- Use when migrating or syncing configuration

### Examples

**Example 1: First Run (Default Mode)**
```bash
cd my_jvagent_app
jvagent
```
This will:
- Create the App node from `app.yaml`
- Install all agents listed in `app.yaml`
- Register all actions for each agent
- Start the server

**Example 2: Subsequent Runs (Default Mode)**
```bash
cd my_jvagent_app
jvagent
```
This will:
- Use existing App node (won't overwrite)
- Skip existing agents (won't overwrite their context)
- Skip existing actions (won't overwrite their context)
- Start the server with existing configuration

**Example 3: Update Existing Configuration**
```bash
cd my_jvagent_app
jvagent --update
```
This will:
- Update App node from `app.yaml`
- Update all agents from their `agent.yaml` files
- Update all actions from their `info.yaml` files
- Apply any changes you made to YAML files

**Example 4: Bootstrap Only (No Server)**
```bash
cd my_jvagent_app
jvagent bootstrap
```
This will:
- Bootstrap the application graph
- Install/update agents and actions
- Exit without starting the server

**Example 5: Bootstrap with Updates**
```bash
cd my_jvagent_app
jvagent bootstrap --update
```
This will:
- Update all agents and actions from YAML files
- Exit without starting the server

### Running Without an App Directory

If you run `jvagent` from a directory without `app.yaml`:
- jvagent will start with a basic App node
- No agents or actions will be automatically installed
- You can manually create agents via the API
- Useful for testing or development without a full app structure

### Best Practices

1. **Always run from your app directory:**
   ```bash
   cd /path/to/my_jvagent_app
   jvagent
   ```

2. **Use default mode for normal operation:**
   - Default mode preserves manual changes
   - Safe to run repeatedly
   - Won't overwrite runtime modifications

3. **Use `--update` when changing YAML files:**
   - After modifying `app.yaml` or `agent.yaml`
   - When syncing configuration from version control
   - During deployment or migration

4. **Keep your app directory in version control:**
   - Track `app.yaml`, `agent.yaml`, and `info.yaml` files
   - Don't commit `.env` or database files
   - Use `.env.example` for documentation

## Core Concepts

### Actions

**Actions** are plugins that extend agent functionality. They:
- Follow a standard interface defined by the `Action` base class
- Are organized in standardized directories under namespaces
- Have their own lifecycle hooks for initialization and cleanup
- Can be enabled/disabled dynamically
- Support type-safe property configuration

### Agents

**Agents** are the primary execution units in jvagent. They:
- Contain one or more actions
- Have their own configuration and metadata
- Are defined via `agent.yaml` descriptors
- Can be dynamically loaded and managed

### Namespaces

**Namespaces** organize actions to prevent naming conflicts:
- Actions are grouped by namespace (e.g., `jvagent`, `contrib`, `custom`)
- Same action name can exist in different namespaces
- Actions are referenced using `namespace/action_name` format

## Directory Structure

```
jvagent_app/
├── app.yaml                    # Application configuration
├── agents/
│   └── {agent_name}/
│       ├── agent.yaml         # Agent configuration
│       └── actions/
│           └── {namespace}/   # Namespace directory
│               └── {action_name}/
│                   ├── info.yaml  # Action metadata
│                   ├── {action_name}.py  # Action implementation
│                   ├── requirements.txt   # Action dependencies
│                   └── README.md
└── .env
```

### Example Structure

```
jvagent_app/
├── app.yaml
├── agents/
│   └── example_agent/
│       ├── agent.yaml
│       └── actions/
│           ├── jvagent/              # Official namespace
│           │   └── example_action/
│           │       ├── info.yaml
│           │       ├── example_action.py
│           │       └── requirements.txt
│           ├── contrib/              # Community namespace
│           │   └── slack_notifier/
│           │       ├── info.yaml
│           │       └── slack_notifier.py
│           └── custom/              # Custom namespace
│               └── custom_action/
│                   ├── info.yaml
│                   └── custom_action.py
```

## Configuration Files

### app.yaml

Application-level configuration that bootstraps the entire jvagent application:

```yaml
# Application reference
app: jvagent_demo_app

# Application metadata
version: 1.0.0
author: Your Name/Organization

# jvagent version requirement (optional)
jvagent: ~2.1.0

# Application context: Properties that configure the App node
context:
  name: jvagent Demo App
  description: Demo application
  file_storage_provider: local
  file_storage_root_dir: ./.files
  file_storage_enabled: true

# Application metadata (not stored in App node)
license: MIT
homepage: https://github.com/your-org/jvagent_demo_app
tags:
  - demo
  - example

# Application configuration defaults
config:
  server:
    host: 0.0.0.0
    port: 8000
  
  database:
    type: json
    path: ./jvdb
  
  file_storage:
    provider: local
    root_dir: ./.files
    enabled: true

# Agents to Install (list of namespace/agent_name strings)
agents:
  - jvagent/example_agent
  - contrib/another_agent
```

### agent.yaml

Agent-level configuration defining the agent and its actions:

```yaml
# Agent reference in namespace/agent_name format
agent: jvagent/example_agent

# Agent metadata
version: 1.0.0
author: Your Name

# jvagent version requirement (optional)
jvagent: ~2.1.0

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
  
  - action: contrib/slack_notifier
    context:
      enabled: true
      webhook_url: "https://hooks.slack.com/..."
```

**Key Points:**
- `agent`: Agent reference in `namespace/agent_name` format
- `context`: Object containing all overridable agent properties (alias, description, enabled, etc.)
- `actions`: List of action assignments, each with `action: namespace/action_name` and `context:` for overridable properties

### info.yaml

Action package descriptor:

```yaml
package:
  # Action name in namespace/action_name format
  # Note: The namespace is also determined by the folder structure
  name: jvagent/example_action
  
  # Package author
  author: Your Name/Organization
  
  # Archetype: The main Action class name (same as the Action Node class)
  archetype: ExampleAction
  
  # Package version
  version: 1.0.0
  
  # Package metadata
  meta:
    title: Example Action
    description: A boilerplate action demonstrating jvagent action structure
    group: jvagent
    type: action
  
  # Package configuration
  config:
    order:
      weight: 0
  
  # Package dependencies
  dependencies:
    # jvagent version requirement
    jvagent: ~2.1.0
    # Other action dependencies (by namespace/action_name)
    actions:
      # - jvagent/another_action: ~1.0.0
```

**Key Points:**
- `package.name`: Action reference in `namespace/action_name` format
- `package.archetype`: The Action class name (must match the class in the Python file)
- `package.meta`: Metadata object with title, description, group, and type
- `package.config`: Configuration object (e.g., for ordering)
- `package.dependencies`: Dependencies object with `jvagent` version and `actions` list
- All configuration should be defined as typed Pydantic fields in your Action class
- Override these properties in agent.yaml using the `context` object

**Key Points:**
- `name`: Action identifier (not `id`)
- `title`: Human-readable display name
- Namespace is determined by folder structure, not in this file
- No `config` section - use typed properties in Action class

## Creating Actions

### Step 1: Create Action Directory

```bash
cd agents/my_agent/actions
mkdir -p jvagent/my_action
cd jvagent/my_action
```

### Step 2: Create info.yaml

```yaml
name: my_action
title: My Action
version: 1.0.0
description: Does something useful
enabled: true

module: my_action
class: MyAction

dependencies:
  python:
    - requests>=2.31.0
  actions: []

lifecycle:
  auto_enable: false
  enable_pulse: true
  pulse_interval: 60
```

### Step 3: Create Action Class

```python
# my_action.py
from typing import Any, Dict
from pydantic import Field

from jvagent.action.action import Action


class MyAction(Action):
    """My custom action implementation."""
    
    # Define type-safe configuration properties
    timeout: int = Field(default=30, description="Operation timeout in seconds", ge=1)
    retries: int = Field(default=3, description="Number of retry attempts", ge=0, le=10)
    api_endpoint: str = Field(default="https://api.example.com", description="API endpoint URL")
    
    async def on_register(self) -> None:
        """Called when action is registered."""
        print(f"MyAction registered:")
        print(f"  Timeout: {self.timeout}s")
        print(f"  Retries: {self.retries}")
        print(f"  API Endpoint: {self.api_endpoint}")
    
    async def on_enable(self) -> None:
        """Called when action is enabled."""
        print(f"MyAction enabled (timeout={self.timeout}s)")
    
    async def on_disable(self) -> None:
        """Called when action is disabled."""
        print("MyAction disabled")
    
    async def execute(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """Execute the action with input data."""
        # Use configuration properties directly
        print(f"Executing with timeout: {self.timeout}s, retries: {self.retries}")
        
        result = {
            "processed": True,
            "input": input_data,
            "output": "Action executed successfully",
            "timeout_used": self.timeout
        }
        
        return result
```

### Step 4: Register in agent.yaml

```yaml
actions:
  - name: jvagent/my_action
    enabled: true
    context:
      timeout: 60
      retries: 5
      api_endpoint: "https://prod.api.example.com"
```

## Action Lifecycle

Actions have well-defined lifecycle hooks:

1. **on_register()** - Called when action is first registered
   - Use for initialization tasks
   - Validate configuration
   - Set up connections

2. **on_enable()** - Called when action is enabled
   - Start background tasks
   - Initialize active resources
   - Connect to external services

3. **post_register()** - Called after all actions are registered
   - Perform cross-action initialization
   - Set up inter-action communication
   - Validate action dependencies

4. **pulse()** - Called periodically for maintenance
   - Perform periodic operations
   - Health checks
   - Cleanup tasks

5. **on_disable()** - Called when action is disabled
   - Stop background tasks
   - Clean up active resources
   - Disconnect from external services

6. **on_reload()** - Called when action is reloaded
   - Refresh configuration
   - Reinitialize resources
   - Update connections

7. **on_deregister()** - Called when action is removed
   - Final cleanup
   - Release resources
   - Close connections

## Property Configuration

### Type-Safe Properties

All action configuration is done through **typed Pydantic fields**, not dictionaries:

```python
class MyAction(Action):
    # Type-safe properties with validation
    timeout: int = Field(default=30, ge=1, le=300)
    api_url: str = Field(default="https://api.example.com")
    retries: int = Field(default=3, ge=0, le=10)
```

**Benefits:**
- ✅ Type validation by Pydantic
- ✅ Clear schema (know what properties exist)
- ✅ IDE autocomplete
- ✅ Runtime type checking
- ✅ Single, clear way to configure actions

### Context-Based Overrides

Properties are overridden in `agent.yaml` using the `context` object:

```yaml
actions:
  - name: jvagent/my_action
    enabled: true
    
    context:
      timeout: 60
      retries: 5
      api_url: "https://prod.api.example.com"
```

**Why Context?**
- Clear separation between configuration metadata (`name`, `enabled`) and properties
- Easy to understand what can be customized
- Scalable for complex actions with many properties
- Self-documenting structure

### Property Resolution

1. Action class defines default values
2. `agent.yaml` `context` overrides defaults
3. Pydantic validates all properties
4. Action instance created with final values

## Namespace System

### Overview

The namespace system organizes actions to prevent naming conflicts and clearly indicate their source.

### Directory Structure

Actions are organized under namespace directories:

```
agents/{agent_name}/actions/
├── jvagent/          # Official jvagent actions
├── contrib/          # Community contributions
├── custom/           # Generic custom actions
└── {vendor}/         # Third-party vendor actions
```

### Namespace Conventions

| Namespace | Purpose | Examples |
|-----------|---------|----------|
| `jvagent` | Official core actions | `example_action`, `file_processor` |
| `contrib` | Community contributed actions | `slack_notifier`, `twitter_bot` |
| `custom` | Generic custom actions | `internal_tool`, `custom_workflow` |
| `{vendor}` | Third-party vendor actions | `openai`, `anthropic`, `aws` |
| `{org}` | Organization-specific | `acme_corp`, `my_company` |

### Action References

Actions are referenced using `namespace/action_name` format:

```yaml
actions:
  - name: jvagent/example_action
  - name: contrib/slack_notifier
  - name: custom/custom_workflow
```

**Benefits:**
- Explicit namespace prevents ambiguity
- Same action name can exist in different namespaces
- Self-documenting configuration
- Copy-paste safe between agents

### Full Action Identity

An action is uniquely identified by:
1. **Agent ID**: Which agent it belongs to
2. **Namespace**: Which namespace it's in
3. **Action Name**: The action's unique identifier

Example: `agent_123 / jvagent / example_action`

## API Usage

### Action CRUD Endpoints

Actions have full CRUD endpoints colocated in the Action class:

- `GET /actions/{action_id}` - Get action by ID
- `PUT /actions/{action_id}` - Update action
- `DELETE /actions/{action_id}` - Delete action
- `GET /actions` - List actions (with pagination)
- `POST /actions/{action_id}/enable` - Enable action
- `POST /actions/{action_id}/disable` - Disable action
- `POST /actions/{action_id}/reload` - Reload action
- `GET /actions/{action_id}/health` - Health check

### Example API Calls

```bash
# List all actions
curl -X GET "http://localhost:8000/actions" \
  -H "Authorization: Bearer {token}"

# Get specific action
curl -X GET "http://localhost:8000/actions/{action_id}" \
  -H "Authorization: Bearer {token}"

# Enable action
curl -X POST "http://localhost:8000/actions/{action_id}/enable" \
  -H "Authorization: Bearer {token}"

# Update action
curl -X PUT "http://localhost:8000/actions/{action_id}" \
  -H "Authorization: Bearer {token}" \
  -H "Content-Type: application/json" \
  -d '{
    "enabled": true,
    "description": "Updated description"
  }'
```

## Development

### Environment Variables

Key environment variables (see `.env.example` for full list):

**Server Configuration:**
- `JVAGENT_HOST` - Server host (default: `127.0.0.1`)
- `JVAGENT_PORT` - Server port (default: `8000`)
- `JVAGENT_TITLE` - API title
- `JVAGENT_VERSION` - Application version

**Database Configuration:**
- `JVSPATIAL_DB_TYPE` - Database type: `json` or `mongodb` (default: `json`)
- `JVSPATIAL_DB_PATH` - Database path (default: `./jvdb`)

**Authentication:**
- `JVAGENT_AUTH_ENABLED` - Enable authentication (default: `true`)
- `JVSPATIAL_JWT_SECRET` - JWT secret key (change in production!)
- `JVSPATIAL_JWT_EXPIRE_MINUTES` - JWT expiration (default: `60`)

**Admin User:**
- `JVAGENT_ADMIN_USERNAME` - Admin username (default: `admin`)
- `JVAGENT_ADMIN_PASSWORD` - Admin password (required)
- `JVAGENT_ADMIN_EMAIL` - Admin email (default: `admin@jvagent.example`)

### Install Development Dependencies

```bash
pip install -e ".[dev]"
```

### Run Tests

```bash
pytest
```

### Code Formatting

```bash
black jvagent/
ruff check jvagent/
```

### Project Structure

```
jvagent/
├── jvagent/              # Main package
│   ├── cli.py            # CLI entry point
│   ├── action/           # Action system
│   │   ├── action.py     # Action base class
│   │   ├── actions.py    # Actions manager
│   │   └── loader.py     # Action loader
│   ├── core/             # Core entities
│   │   ├── app.py        # App node
│   │   ├── agent.py      # Agent node
│   │   ├── agents.py     # Agents manager
│   │   ├── loader.py     # Agent loader
│   │   └── app_loader.py # App loader
│   └── version.py        # Version info
├── .env.example          # Environment template
├── pyproject.toml        # Package configuration
└── README.md             # This file
```

## What Happens on Startup

When jvagent starts, it automatically:

1. **Bootstraps the application graph**:
   - Creates an `App` node (if it doesn't exist)
   - Creates an `Agents` node (if it doesn't exist)
   - Connects `App` to the Root node
   - Connects `Agents` to `App`

2. **Loads application configuration**:
   - Reads `app.yaml` if present
   - Installs/updates agents from configuration

3. **Discovers and loads actions**:
   - Scans `agents/{agent_name}/actions/` for namespaces
   - Discovers actions from `info.yaml` files
   - Loads action classes dynamically
   - Applies configuration from `agent.yaml`

4. **Creates admin user** (if it doesn't exist):
   - Uses credentials from `.env` file
   - Hashed password stored securely

## Best Practices

### 1. Use Type-Safe Properties

```python
# Good: Type-safe properties
class MyAction(Action):
    timeout: int = Field(default=30, ge=1, le=300)
    api_url: str = Field(default="https://api.example.com")

# Avoid: Unvalidated dictionary
class MyAction(Action):
    config: Dict[str, Any] = Field(default_factory=dict)
```

### 2. Use Context for Property Overrides

```yaml
# Good: Properties in context
actions:
  - name: jvagent/my_action
    enabled: true
    context:
      timeout: 60

# Avoid: Mixing levels
actions:
  - name: jvagent/my_action
    enabled: true
    timeout: 60  # Should be in context
```

### 3. Use Namespace/Action_Name Format

```yaml
# Good: Explicit namespace
actions:
  - name: jvagent/example_action

# Avoid: Missing namespace
actions:
  - name: example_action
```

### 4. Document Your Properties

```python
class MyAction(Action):
    timeout: int = Field(
        default=30,
        description="Operation timeout in seconds",
        ge=1,
        le=300
    )
```

### 5. Provide Sensible Defaults

```python
class MyAction(Action):
    # Good: Always provide defaults
    timeout: int = Field(default=30)
    
    # Not recommended: Forces user to always provide value
    # required_setting: str = Field(...)
```

## License

MIT License - see [LICENSE](LICENSE) file for details.
