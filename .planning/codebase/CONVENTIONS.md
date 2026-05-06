# Coding Conventions

**Analysis Date:** 2026-05-06

## Naming Patterns

**Files:**
- Snake case for all module files: `action_loader.py`, `interact_walker.py`, `app_yaml_validator.py`
- Action implementations: `{action_name}_action.py` (e.g., `persona_action.py`, `skill_action.py`)
- Test files: `test_*.py` (e.g., `test_startup.py`, `test_graph_repair.py`)
- Utility/shared modules: descriptive snake_case (e.g., `graph_traversal.py`, `response_builder.py`)

**Functions:**
- Snake case for all functions: `get_jvagent_app_id()`, `trigger_task_created_callback()`, `_safe_webhook_target()`
- Private functions prefixed with single underscore: `_validate_webhook_url()`, `_dead_edge_data()`, `_repair_to_completion()`
- Lifecycle hooks: `on_register()`, `on_enable()`, `on_disable()`, `on_deregister()`, `on_startup()`, `post_register()`, `healthcheck()`
- Async functions use `async def`: `async def trigger_task_created_callback()`, `async def test_repair_returns_expected_structure()`

**Variables:**
- Snake case throughout: `webhook_url`, `agent_id`, `conversation_id`, `query_params`, `is_streaming`
- Constants in UPPER_SNAKE_CASE: `_SSRF_BLOCKED_NETWORKS`, `_REPAIR_MAX_STEPS`, `_THINKING_END`, `_QUERY_KWARGS_BLOCKLIST`
- Private module-level state with leading underscore: `_startup_completed`, `_SEEN_WARNING_KEYS`

**Types and Classes:**
- PascalCase for all classes: `Action`, `InteractWalker`, `InteractionInitResult`, `PersonaAction`, `LanguageModelAction`
- Exception classes: PascalCase ending in Error/Exception: `ValueError`, `TypeError`, `AssertionError`
- Type aliases and dataclasses use PascalCase: `ModelActionResult`, `ContentPart`, `MessageContent`
- Generic type variables in UPPER_SNAKE_CASE: `T = TypeVar("T", bound="Action")`

## Code Style

**Formatting:**
- Tool: Black 24.8.0 (strict enforcement via pre-commit)
- Line length: 88 characters
- Single quotes preferred but not enforced (Black respects existing style)
- Trailing commas for multiline collections
- Spacing around operators and after commas

**Example formatted code:**
```python
async def trigger_task_created_callback(
    conversation: Any, task_entry: Dict[str, Any]
) -> None:
    """Fire a webhook callback whenever a proactive task is created or updated as active."""
    try:
        agent = await conversation.get_agent()
        if not agent:
            return
        webhook_url = None
    except Exception as e:
        logger.error(f"Callback trigger failed: {e}")
```

**Linting:**
- Tool: flake8 6.1.0 with extensive plugins
- Config file: `.flake8`
- Plugins in use:
  - `pep8-naming`: naming convention checks
  - `flake8_docstrings`: docstring format (D1xx-D4xx codes)
  - `flake8_comprehensions`: comprehension style
  - `flake8_bugbear`: bug detection and anti-patterns
  - `flake8_annotations`: type annotation checks
  - `flake8_simplify`: code simplification suggestions

**Ignored codes** (per `.flake8`):
- E203, E265, E266, E501 (whitespace/line-length exceptions for Black compatibility)
- W503, W291, W293 (whitespace exceptions)
- F401, F403, F405, F811, F841 (unused/undefined imports, variables)
- E402 (module-level import not at top - ignored in tests)
- ANN101, ANN102, ANN001+ (type annotation flexibilities)
- D100-D107, D200-D209 (docstring format - relaxed)
- N802, N803, N805 (naming convention exceptions for certain patterns)
- B001-B036 (bugbear exceptions)
- SIM102, SIM105, SIM110+ (simplify exceptions)

Test files exclude stricter rules; flake8 doesn't run on tests directory.

## Import Organization

**Order:**
1. Standard library imports (sys, os, logging, etc.)
2. Third-party imports (jvspatial, pydantic, httpx, aiohttp, etc.)
3. jvagent internal imports (jvagent.*)

**Pattern:**
```python
import asyncio
import logging
import os
from datetime import datetime
from typing import Any, Dict, Optional

from jvspatial.core import Node, Walker, on_visit
from jvspatial.core.annotations import attribute

from jvagent.action.base import Action
from jvagent.memory.interaction import Interaction
```

**Practices:**
- Import statements sorted alphabetically within each group (enforced by isort with Black profile)
- `isort` 6.0.0 configured with `--profile black` for compatibility
- TYPE_CHECKING imports used to avoid circular dependencies: `if TYPE_CHECKING: from jvagent.memory.interaction import Interaction`
- Relative imports avoided; always use absolute imports from package root
- Module-level private imports allowed in try/except blocks for conditional availability

**Path aliases:**
- No path aliases defined in pyproject.toml
- Absolute imports from package root: `from jvagent.action.base import Action`

## Docstrings

**Format:** Google-style docstrings (enforced by flake8-docstrings, though relaxed)

**Examples:**
```python
def get_jvagent_app_id() -> Optional[str]:
    """Return JVAGENT_APP_ID if set (from .env or os.environ), else None.

    Uses dotenv_values for .env so it works in child processes (e.g. uvicorn --reload)
    where load_dotenv may not have run.
    """

async def trigger_task_created_callback(
    conversation: Any, task_entry: Dict[str, Any]
) -> None:
    """Fire a webhook callback whenever a proactive task is created or updated as active.

    This follows the pattern of push-based task triggers to avoid global database polling.
    """

def _validate_webhook_url(webhook_url: str) -> None:
    """Raise ValueError if *webhook_url* resolves to a private or reserved IP address."""
```

**Rules:**
- Module-level docstrings required (though D100 ignored in flake8 config)
- Function/method docstrings required for public APIs
- One-line docstrings for simple functions are acceptable
- Multi-paragraph docstrings for complex logic
- Code examples in docstrings wrapped in triple backticks with language tags

## Type Hints

**Pattern:** Type hints used throughout, especially in public APIs and core modules

**Examples:**
```python
# Full type hints
async def query_sync(self, prompt: str) -> ModelActionResult:
    """Query the model with prompt."""

def __init__(
    self,
    response: Optional[str] = None,
    stream: Optional[AsyncGenerator[str, None]] = None,
    usage: Optional[Dict[str, int]] = None,
) -> None:
    """Initialize a model action result."""

# TYPE_CHECKING pattern for circular imports
if TYPE_CHECKING:
    from jvagent.memory.interaction import Interaction
```

**Practices:**
- Optional types use `Optional[T]` or `Union[T, None]`
- Collection types include generic parameters: `List[Dict[str, Any]]`, `Dict[str, int]`
- Return type `-> None` for functions without return value
- Dataclass fields use type annotations
- mypy configured with `ignore_errors = true` for large action modules but `disallow_untyped_defs = true` for core modules like `app_context` and `env`

## Error Handling

**Patterns:**
- Specific exception catching (not bare except unless necessary with pass/return)
- ValueError for validation failures: `raise ValueError(f"Webhook URL has no resolvable host: {webhook_url}")`
- Custom exceptions rare; mostly use built-in exceptions
- Broad exception handlers often log and return gracefully rather than propagate

**Example:**
```python
try:
    addrs = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
except socket.gaierror as e:
    raise ValueError(f"Webhook URL hostname resolution failed: {hostname}: {e}")

try:
    # Attempt to load config from .env
    values = dotenv_values(candidate)
    val = values.get("JVAGENT_APP_ID") if values else None
except Exception:
    pass  # Fall through to os.environ
```

**For Async:**
- Use `AsyncMock` from unittest.mock for testing async functions
- Async contexts properly cleaned up with `async with`
- Graceful degradation in callbacks that fire asynchronously

## Logging

**Framework:** Python's standard `logging` module

**Pattern:**
- Logger created at module level: `logger = logging.getLogger(__name__)`
- Used for warnings, errors, debug info: `logger.warning()`, `logger.error()`, `logger.debug()`
- Informational logging at `debug` or `warning` level depending on importance
- No print() statements in library code

**Examples:**
```python
logger = logging.getLogger(__name__)

# In code
logger.warning(f"Error discovering endpoints for action {self.id}: {e}")
logger.debug(f"Skipping core module: {module_name}")
logger.error(f"Callback trigger failed: {e}")
```

## Comments

**When to Comment:**
- Complex algorithms or non-obvious business logic
- Explain *why* code is doing something, not *what* it's doing
- Reserved IP block explanations: `# Reserved IP blocks that outbound webhooks must not target`
- Sentinel markers: `# Sentinel end-of-stream marker for thinking/reasoning delta queues`
- Status flags and transitions: `# Immediate persistence: deferred Interaction/Conversation saves break tests...`

**JSDoc/TSDoc:**
- Python uses docstrings instead of JSDoc
- Google-style docstrings with parameter/return descriptions

## Function Design

**Size:** Functions vary widely from 3 lines to 300+ lines

**File-specific patterns:**
- `action/base.py` (1118 lines): Large core base class with multiple lifecycle hooks and helpers
- `skill_action.py` (3200 lines): Very large, monolithic action with complex state management
- Most utility functions: 10-50 lines
- Test functions: 5-40 lines (tighter, focused tests)

**Parameters:**
- Use typed parameters with optional defaults
- Common pattern: `Dict[str, Any]` for flexible data structures
- Async functions receive agents/contexts as parameters rather than global state
- Variadic parameters (`*args`, `**kwargs`) used for tool execution and flexible APIs

**Return Values:**
- Explicit return types in signature
- None for side-effect functions
- Dictionaries for multi-value returns: `Dict[str, Any]`
- Dataclasses for structured results: `ModelActionResult`, `InteractionInitResult`

## Module Design

**Exports:**
- `__init__.py` files explicitly import and export public interfaces
- Example from `jvagent/action/interact/__init__.py`:
  ```python
  from jvagent.action.interact.base import InteractAction
  from jvagent.action.interact.interact_walker import InteractWalker
  
  __all__ = ["InteractWalker", "InteractAction"]
  ```

**Barrel Files:**
- Used sparingly, mostly for action module organization
- `actions.py` serves as registry and barrel export

**Module Structure:**
- Core implementation in main module: `interact_walker.py`
- Supporting utilities in related files: `response_builder.py`, `rate_limiter.py`
- Endpoints in `endpoints.py` for API exposure
- Base classes in `base.py`

## Attribute/Property Pattern

**jvspatial Node Attributes:**
- Action configuration uses jvspatial's `attribute()` system
- Defined as class-level attributes on Action subclasses
- Supports typed defaults and descriptions

**Example:**
```python
class LanguageModelAction(BaseModelAction):
    model: str = attribute(
        default="gpt-4",
        description="Model identifier"
    )
    max_tokens: int = attribute(
        default=2048,
        description="Maximum tokens in response"
    )
```

## Dataclass Pattern

**Use cases:**
- Immutable result objects: `@dataclass(frozen=True) class InteractionInitResult`
- Walker state: `@dataclass class InteractWalker` (not frozen, mutable state)
- Type aliases for clarity: `ContentPart`, `MessageContent`

**Examples:**
```python
@dataclass(frozen=True)
class InteractionInitResult:
    """Outcome of InteractWalker.initialize_interaction."""
    ok: bool
    code: str
    detail: Optional[str] = None
```

## Async/Await

**Patterns:**
- All async operations properly awaited
- No fire-and-forget tasks unless explicitly background
- `AsyncMock` for testing async functions
- Walker.spawn() for starting async walks
- Async generators for streaming: `AsyncGenerator[str, None]`

---

*Convention analysis: 2026-05-06*
