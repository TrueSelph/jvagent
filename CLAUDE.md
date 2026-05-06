# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Development Commands

### Installation
- Install in development mode: `pip install -e .`
- Install with development dependencies: `pip install -e ".[dev]"`

### Testing & Quality
- Run all tests: `pytest tests/`
- Run pre-commit hooks: `pre-commit run --all-files`
- Code formatting: `black jvagent/`
- Import sorting: `isort jvagent/ --profile black`
- Linting: `flake8 jvagent/ --config=.flake8`
- Type checking: `mypy jvagent/`

### Running jvagent
- Start server: `jvagent` or `python -m jvagent`
- Run specific app: `jvagent /path/to/app_directory`
- Bootstrap app without server: `jvagent /path/to/app_directory bootstrap`
- Update app configuration (merge): `jvagent /path/to/app_directory --update`
- Reset app from source (destructive): `jvagent /path/to/app_directory --update --source`
- Purge database (dev only): `jvagent /path/to/app_directory --purge`
- Generate Dockerfile: `jvagent bundle /path/to/app_directory`

## Architecture Overview

jvagent is a modular AI agent platform built on `jvspatial`'s graph-based primitives. It uses a declarative configuration system (YAML) to define applications, agents, and actions.

### Graph Hierarchy
`Root` $\to$ `App` $\to$ `Agents` $\to$ `Agent`
- `Agent` $\to$ `Memory` $\to$ `User` $\to$ `Conversation` $\to$ `Interaction` (bidirectionally chained)
- `Agent` $\to$ `Actions` $\to$ `Action` (registered plugins)

### Core Components
- **App**: Singleton root node managing app-level settings, timezones, and file storage.
- **Agents**: Logical execution units. Defined via `agent.yaml`, they orchestrate a set of Actions.
- **Actions**: Pluggable modules.
    - `Action` (Base): Core logic and lifecycle hooks (`on_register`, `on_enable`, etc.).
    - `InteractAction`: Specialized actions for the interaction subsystem, used by the `InteractWalker`.
    - **Cockpit** (`jvagent/cockpit_interact_action`): unified router + converse + skill loop; see [docs/COCKPIT.md](docs/COCKPIT.md).
- **Memory**: Manages user state and conversation history with a rolling window pruning mechanism (`interaction_limit`).
- **Namespaces**: Prevents naming conflicts using `namespace/action_name` format (e.g., `jvagent/`, `contrib/`, `custom/`).

### Action Development Pattern
Actions follow a strict directory structure:
`actions/{namespace}/{action_name}/`
- `{action_name}.py`: Implementation of the `Action` subclass.
- `endpoints.py`: API endpoints decorated with `@endpoint`.
- `info.yaml`: Package metadata and dependencies.
- `__init__.py`: Exports the Action class and imports `endpoints.py` for discovery.

### Configuration Resolution
1. **Defaults**: Defined as Pydantic `attribute` fields in the Action class.
2. **Overrides**: Provided in the `context` block of `agent.yaml`.
3. **Validation**: Handled by Pydantic at runtime.

**Model HTTP retries**: `BaseModelAction` / `LanguageModelAction` support configurable retries for transient timeouts and transport errors (`max_retries`, `retry_initial_delay`, `retry_max_delay`, `retry_backoff_multiplier`, `retry_jitter`, `retry_on_status_codes`). Defaults apply to all LM providers; tune per action in `agent.yaml`. See [docs/language-models.md](docs/language-models.md).
