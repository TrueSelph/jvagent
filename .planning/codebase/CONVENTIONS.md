# Coding Conventions

**Analysis Date:** 2026-05-06

## Naming Patterns

**Files:**
- `snake_case.py` for all Python modules (e.g., `agent_loader.py`, `skill_action.py`, `tool_executor.py`)
- Test files prefixed with `test_` (e.g., `test_skill_action_core.py`, `test_pruning_fix.py`)
- Action implementation files match the action name: `actions/{namespace}/{action_name}/{action_name}.py`
- Endpoint modules consistently named `endpoints.py` (`jvagent/core/endpoints.py`, `jvagent/action/interact/endpoints.py`)
- Package descriptors named `info.yaml` per action package (e.g., `jvagent/action/persona/info.yaml`)
- YAML config files: `agent.yaml`, `app.yaml`, `info.yaml`, `*.yaml` (lowercase)

**Classes:**
- `PascalCase` for all classes (`Agent`, `Action`, `SkillAction`, `Memory`, `Conversation`, `InteractWalker`)
- Action subclasses end in `Action`: `PersonaAction`, `SkillAction`, `AgentInteractAction`
- Walker subclasses end in `Walker`: `InteractWalker`, `QuestionPathWalker`
- Test classes prefixed with `Test`: `TestSkillRunConfig`, `TestTaskStoreCreate`, `TestRecoveryPolicy`
- Pydantic-style config dataclasses use `Config` suffix: `SkillRunConfig`, `CompactorConfig`

**Functions / Methods:**
- `snake_case` for functions and methods (`get_action`, `run_to_completion`, `refresh_memory_counters_from_graph`)
- Async functions use `async def` and follow the same `snake_case` convention
- Private helpers prefixed with leading underscore (`_get_user_unlocked`, `_prune_session`, `_clear_jvspatial_load_env_cache`)
- Static class helpers also use leading underscore for private status (`SkillAction._reorder_task_calls_dependency_first`, `SkillAction._apply_plan_first_tool_gate`)

**Variables / Constants:**
- `snake_case` for variables and parameters
- `UPPER_SNAKE_CASE` for module-level constants and frozensets (`DISPATCH = frozenset({...})` in `jvagent/cli/main.py`)
- Type aliases use `PascalCase` with `TypeVar`: `T = TypeVar("T", bound="Action")`, `TAgent = TypeVar("TAgent", bound="Agent")`

**Types / Generics:**
- TypeVars conventionally suffixed with the bound class name (`TAgent`, `T`)
- Generic types from `typing` are explicitly imported (`Optional`, `Dict`, `List`, `Any`, `Union`, `Type`, `TypeVar`, `Sequence`, `TYPE_CHECKING`)

## Code Style

**Formatting:**
- `black` (>=23.9.0, pinned to 24.8.0 in pre-commit) — line length 88
- Target Python versions: `py38`, `py39`, `py310`, `py311`, `py312` (declared in `[tool.black]` in `pyproject.toml`)
- `isort` with `--profile black` and matching `line_length = 88` (`pyproject.toml` `[tool.isort]`)
- Trailing whitespace stripped via `pre-commit-hooks/trailing-whitespace`
- YAML and JSON files validated by `check-yaml --allow-multiple-documents` and `check-json`

**Linting:**
- `flake8 >=6.0.0` with `pep8-naming`, `flake8-docstrings`, `flake8-comprehensions`, `flake8-bugbear`, `flake8-annotations`, `flake8-simplify`
- Config in `.flake8`: `max-line-length = 88`, with a deliberately wide `extend-ignore` list disabling docstring requirements (`D100-D403`), naming checks (`N802-N815`), bugbear stylistic checks (`B001-B036`), comprehension nudges (`C416`), simplify hints (`SIM102-SIM908`), annotation requirements (`ANN001-ANN205`), and unused-import warnings (`F401-F841`)
- Excludes: `.git`, `__pycache__`, `.venv`, `.pytest_cache`, `.vscode`, `build`, `dist`, `docs`, `migrations`, `examples`
- Tests get an explicit per-file ignore: `tests/*: E402` (allows late imports after path manipulation)

**Type Checking:**
- `mypy >=1.6.0` with Python 3.9 baseline (`pyproject.toml` `[tool.mypy]`)
- Lenient defaults: `disallow_untyped_defs = false`, `check_untyped_defs = false`, `no_implicit_optional = false`, `warn_unused_ignores = false`
- Strict overrides for select modules: `jvagent.core.app_context`, `jvagent.env` (require `disallow_untyped_defs = true`)
- Many subsystems explicitly opted out via `ignore_errors = true` overrides (e.g., `jvagent.action.model.*`, `jvagent.action.skill.*` (implicit through wildcard parents), `jvagent.core.*`, `jvagent.memory.*`, `jvagent.cli`)
- Third-party stubs missing → `ignore_missing_imports = true` for `httpx.*`, `jinja2.*`, `jvspatial.*`, `tiktoken`, `aiohttp.*`, `filetype.*`, `elevenlabs.*`, `openai.*`, `pymupdf.*`, `pypdf.*`, `yaml`, `mcp.*`, `typesense.*`
- mypy excludes: `examples/`, `tests/`
- Pre-commit mypy uses `--follow-imports=silent --ignore-missing-imports --explicit-package-bases --no-warn-return-any --no-strict-optional --allow-redefinition --show-error-codes` against the `jvagent` package
- Additional dependencies installed for mypy hook: `types-PyYAML`, `types-requests`

**Secret Detection:**
- `detect-secrets >=1.5.0` runs on `.py`, `.txt`, `.yaml`, `.json` files
- Excludes: `venv`, `.mypy_cache`, `.test_dbs`, `tests`, `examples`, `pnpm-lock.yaml`

## Import Organization

**Order (enforced by isort, profile=black):**
1. `__future__` imports (`from __future__ import annotations` — used in 156 modules)
2. Standard library (`import asyncio`, `import logging`, `import os`, `from typing import ...`, `from datetime import ...`)
3. Third-party (`from jvspatial.core import Node`, `from jvspatial.core.annotations import attribute`, `from fastapi import Request`)
4. First-party (`from jvagent.core.agent import Agent`, `from jvagent.action.skill.skill_action import SkillAction`)

**Style:**
- Prefer `from X import Y` over `import X` for symbols actually used
- Group `TYPE_CHECKING` imports at module top to avoid runtime circular imports:
  ```python
  if TYPE_CHECKING:
      from jvagent.action.actions import Actions
      from jvagent.action.response.response_bus import ResponseBus
  ```
- Local (in-function) imports used when the dependency is heavy or only used on a specific code path (see `jvagent/memory/manager.py`: `from jvagent.memory.lock_manager import get_user_lock_manager` inside `get_user`)
- Path aliases: none — imports are always absolute starting with `jvagent.` or `jvspatial.`

## Type Annotations

**Coverage:**
- Public methods almost always annotated: `async def get_user(self, user_id: str, create_if_missing: bool = True) -> Optional["User"]`
- Class attributes declared with `attribute(...)` from `jvspatial.core.annotations` (e.g., `enabled: bool = attribute(default=True, description="...")`)
- Type-only imports gated behind `if TYPE_CHECKING:` to avoid circulars
- `Optional[X]` is preferred over `X | None` (codebase still supports Python 3.8)
- `Any` used sparingly when interfacing with external SDKs or generic JSON payloads

**Generics:**
- Bounded TypeVars used for inheritance-aware return types: `T = TypeVar("T", bound="Action")`, `TAgent = TypeVar("TAgent", bound="Agent")`
- Methods like `Agent.get` use `cls: Type[TAgent]` and return `Optional[TAgent]` so subclasses retain their type
- Forward references quoted: `Optional["User"]`, `Optional["Conversation"]`

## Error Handling

**Patterns:**
- Domain exceptions imported from `jvspatial.api.exceptions`: `JVSpatialAPIException`, `RateLimitError`, `ResourceNotFoundError`, `ValidationError` (used across `jvagent/action/interact/endpoints.py`)
- Built-in exceptions raised by name (`ValueError`, `TypeError`, `RuntimeError`, `NotImplementedError`) — ~210 instances
- Bare `except:` is permitted by flake8 config (`E722` ignored) but `except Exception:` is the dominant style
- Lifecycle hooks log and swallow errors so a single bad action does not bring down the agent (see `Action.on_register`, `on_enable` patterns in `jvagent/action/base.py`)
- Logger captures stack traces via `logger.exception(...)` when handlers want full tracebacks; `traceback` module imported in `jvagent/action/base.py` for explicit serialization

**Validation:**
- YAML inputs validated through dedicated validator modules (`jvagent/core/agent_yaml_validator.py`, `jvagent/core/app_yaml_validator.py`)
- Recovery policy classifies failures explicitly (`jvagent/action/skill/recovery_policy.py`): `is_recoverable(exc)` returns boolean based on error message heuristics
- Per-iteration retry budgets capped via `RecoveryPolicy(phase_retry_budgets={"model_call": 1})`

## Logging

**Framework:**
- Standard library `logging` module
- Standard pattern at top of every module: `logger = logging.getLogger(__name__)` (~247 modules)
- Standardized configuration via `jvspatial.logging.configure_standard_logging` invoked once in `jvagent/cli/main.py`:
  ```python
  configure_standard_logging(
      level=env("JVSPATIAL_LOG_LEVEL", default="INFO"),
      enable_colors=True,
      preserve_handler_class_names=["DBLogHandler", "StartupLogCounter"],
  )
  ```
- Asyncio noise suppressed: `logging.getLogger("asyncio").setLevel(logging.WARNING)`

**Levels:**
- `logger.info` for lifecycle and high-signal state changes
- `logger.warning` for recoverable anomalies
- `logger.error` for definite failures
- `logger.debug` for diagnostic detail
- `logger.exception` inside `except` blocks to include traceback
- Total logger calls: ~1,425

## Comments and Docstrings

**Module Docstrings:**
- Every public module starts with a triple-quoted module docstring (e.g., `"""Agent node and CRUD operations."""` in `jvagent/core/agent.py`, multi-paragraph block in `jvagent/action/skill/skill_action.py`)
- Module docstrings often describe both intent and architecture (`jvagent/action/agent_interact/agent_interact_action.py` has a multi-section docstring covering Phase 1/Phase 2)

**Class Docstrings:**
- Google-style with `Attributes:`, `Lifecycle Hooks:`, `Action-to-Action Communication:`, `Child Nodes:`, `Note:` sections
- See `jvagent/action/base.py` (~70-line docstring on `Action`) and `jvagent/memory/manager.py` (`Memory`) for canonical examples
- `Args:`, `Returns:`, `Raises:` sections used on most non-trivial methods

**Inline Comments:**
- Used sparingly for non-obvious decisions (e.g., `# Immediate persistence: deferred Interaction/Conversation saves break tests` in `tests/conftest.py`)
- TODO/FIXME comments are nearly absent: only 1 `TODO|FIXME|HACK|XXX` marker found in the entire `jvagent/` source tree

**Action README:**
- Some actions ship a `README.md` next to the implementation (e.g., `jvagent/action/agent_interact/README.md`, `jvagent/memory/README.md`)

## Function Design

**Async First:**
- ~5,561 occurrences of `async def`/`await`/`asyncio` across `jvagent/`
- Database access, lifecycle hooks, and graph traversals are all async
- Synchronous helpers exist for pure data transforms (e.g., `SkillAction._reorder_task_calls_dependency_first`)

**Size:**
- Most public methods 5-30 lines
- Long files (e.g., `jvagent/action/model/language/openai/openai.py` at 685 lines, `ollama.py` at 475 lines) reflect provider integration surface
- Private static helpers used to keep large coordinator classes (`SkillAction`) testable in isolation

**Parameters:**
- Keyword-only arguments preferred for boolean flags and configuration objects
- Default values inline; complex defaults use `default_factory` (`metadata: Dict[str, Any] = attribute(default_factory=dict, ...)`)
- `Optional[X]` parameters explicitly declared rather than relying on implicit-Optional

**Return Values:**
- Async getters return `Optional[X]` and never raise on missing entities (returning `None` instead)
- Methods that perform mutations and need to signal failure tend to return `bool` (`startup.run_app_startup() -> bool`)

## Module / Class Design

**Singleton Pattern:**
- `App` is a singleton root node (`jvagent/core/app.py`); enforced via `App.get()` returning the existing instance
- Many actions declare `singleton: true` in `info.yaml` (e.g., `jvagent/action/persona/info.yaml`)

**Pydantic Attributes:**
- Action configuration uses `attribute(default=..., description=..., indexed=..., protected=..., private=...)` from `jvspatial.core.annotations`
- `protected=True` marks fields that bulk YAML overwrites must skip (e.g., `App.update_mode`)
- `private=True` marks transient runtime instances (e.g., `Agent._response_bus`)
- `indexed=True` and `index_unique=True` declare database indexes co-located with the model

**Composition over Inheritance:**
- `SkillInteractAction` adapts the interact subsystem to the engine in `SkillAction` via composition (see module docstring in `jvagent/action/skill/skill_action.py`)
- `BaseModelAction` and `LanguageModelAction` provide HTTP retry primitives that concrete provider classes inherit

**Lifecycle Hooks:**
- Documented standard set on `Action`: `on_register`, `on_reload`, `post_register`, `on_startup`, `on_enable`, `on_disable`, `on_deregister`, `healthcheck`

**Exports:**
- Top-level `jvagent/__init__.py` only exports `__version__`
- Sub-packages re-export selectively through their `__init__.py`
- No "barrel" star-imports

## Configuration Layering

1. **Defaults:** declared as Pydantic `attribute` fields on the Action subclass
2. **YAML overrides:** `agent.yaml` `context:` block per action
3. **Env-var resolution:** `${ENV_VAR}` placeholders resolved by `jvagent/core/env_resolver.py`
4. **Runtime mutation:** typed setters (e.g., `set_app_update_mode`) for `protected` fields

## Endpoint Convention

- All HTTP endpoints declared with the `@endpoint` decorator from `jvspatial.api.endpoint`
- Endpoint files always named `endpoints.py` and imported by their `__init__.py` for discovery
- Standard parameters: path, `methods=[...]`, `auth=True`, `roles=[...]`, `tags=[...]`, `response=success_response(...)`
- Response shape declared with `ResponseField(field_type=..., description=..., example=...)` for OpenAPI docs
- See `jvagent/core/endpoints.py:29` (`get_agent`) and `jvagent/action/interact/endpoints.py` for canonical patterns

## Pre-Commit Pipeline

**Hooks executed in order (`.pre-commit-config.yaml`):**
1. `pre-commit-hooks v2.3.0`: `check-yaml --allow-multiple-documents`, `check-json`, `trailing-whitespace`
2. `psf/black v24.8.0` (excludes `venv`, `examples`)
3. `PyCQA/isort v6.0.0` with `--profile black` (excludes `venv`, `examples`)
4. `PyCQA/flake8 v6.1.0` with the six flake8 plugins, config `.flake8` (excludes `venv`, `examples`, `tests`)
5. `mirrors-mypy v1.10.1` against `jvagent` package (excludes `venv`, `tests`, `examples`)
6. `local pytest` (manual stage only, runs `pytest tests/ -v --tb=short`)
7. `Yelp/detect-secrets v1.5.0` against `.py|.txt|.yaml|.json`

## CI Pipeline

**`.github/workflows/test-jvagent.yaml`:**
- Triggers: pull requests and pushes to `main`
- Runner: `ubuntu-latest`, Python 3.11
- Steps:
  1. `pip install -e '.[test]'`
  2. Record installed `jvspatial` version (integration smoke)
  3. `python -m jvagent.cli validate examples/jvagent_app` (YAML validation)
  4. `pytest tests/ -v --tb=short`
  5. `pre-commit run --all-files`

---

*Convention analysis: 2026-05-06*
