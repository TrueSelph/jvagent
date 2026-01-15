# Example Action

This is a boilerplate action demonstrating the structure and implementation of a custom action in jvagent.

## Overview

The Example Action demonstrates:
- Basic action structure extending `Action`
- Lifecycle hooks (on_register, on_enable, on_disable, etc.)
- Configuration access
- File operations via App node
- Custom execution methods

## Structure

```
example_interact_action/
├── __init__.py         # Package initialization (imports action class and endpoints)
├── example_interact_action.py   # Main action implementation (Interact Action class)
├── endpoints.py        # API endpoints for this action
├── info.yaml          # Action metadata
├── requirements.txt   # Python dependencies
└── README.md         # This file
```

### File Organization

- **`__init__.py`**: Package initialization file that imports the action class and endpoints module. This ensures endpoints are discovered when the action package is loaded. This is the standard pattern for jvagent actions.
- **`example_action.py`**: Contains the `ExampleAction` class with business logic, lifecycle hooks, and configuration properties
- **`endpoints.py`**: Contains all HTTP API endpoints decorated with `@endpoint`. This file is imported by `__init__.py` to ensure endpoints are discovered
- **`info.yaml`**: Action metadata and package information
- **`requirements.txt`**: Python package dependencies

## Configuration

Configuration is defined in `info.yaml`:

```yaml
id: example_action
name: Example Action
version: 1.0.0
enabled: true
config:
  timeout: 30
  retries: 3
```

## Usage

Once installed, this action can be:
1. Assigned to agents via the `actions` section in `agent.yaml`
2. Enabled/disabled via API or configuration
3. Executed through agent workflows

## Development

To customize this action:

1. Update `info.yaml` with your action metadata
2. Modify `example_action.py` to implement your logic
3. Add API endpoints in `endpoints.py` (see "API Endpoints" section below)
4. Add dependencies to `requirements.txt`
5. Update this README with your action's documentation

### API Endpoints

All HTTP endpoints for this action are defined in `endpoints.py`. This follows the standard pattern for jvagent actions:

- **Separation of concerns**: Business logic in the action class, API endpoints in a separate file
- **Package initialization**: `__init__.py` imports both the action class and endpoints module
- **Automatic discovery**: Endpoints are discovered when the action package is loaded (via `__init__.py`)
- **Clean organization**: Keeps the action class focused on core functionality

**Standard Pattern:**

1. **`__init__.py`** imports the action class and endpoints:
   ```python
   from .example_action import ExampleAction
   from . import endpoints  # noqa: F401
   ```

2. **`endpoints.py`** defines all HTTP endpoints:
   ```python
   from jvspatial.api import endpoint
   from .example_action import ExampleAction

   @endpoint("/actions/{action_id}/my_endpoint", methods=["POST"], auth=True)
   async def my_endpoint(action_id: str):
       action = await ExampleAction.get(action_id)
       # ... endpoint logic
   ```

This pattern allows actions to have multiple Python modules while ensuring endpoints are always discovered.

## Lifecycle Hooks

- `on_register()`: Called when action is registered
- `on_enable()`: Called when action is enabled
- `on_disable()`: Called when action is disabled
- `on_reload()`: Called when action is reloaded
- `post_register()`: Called after all actions are registered
- `pulse()`: Called periodically for background operations
- `analytics()`: Called to gather analytics data
- `healthcheck()`: Called to perform health checks
