"""ToolExecutor: bridges ModelActionResult.tool_calls to actual execution.

Reuses existing ToolManager, ToolDefinition, ToolCall, and MCPClientWrapper.
Bypasses MCPAction.fulfill() (which does its own NL-to-tool mapping) and calls
MCPClientWrapper.call_tool() directly for deterministic LLM-driven dispatch.

Observability
-------------
Each dispatch produces a ``ToolExecutionEnvelope`` capturing attempt id,
input fingerprint, latency, error class, and a ``recoverable`` flag.  These
envelopes are accumulated in ``ToolExecutor.envelopes`` for the duration of
the loop run and can be linked to task steps and the EvidenceLog.
"""

import asyncio
import contextlib
import fnmatch
import hashlib
import importlib.util
import inspect
import logging
import os
import re
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from jvagent.action.mcp.mcp_action import _normalize_call_result
from jvagent.action.model.language.tools import ToolCall, ToolDefinition, ToolManager
from jvagent.action.skill.tool_registry import ToolRegistry

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Observability envelope
# ---------------------------------------------------------------------------


@dataclass
class ToolExecutionEnvelope:
    """Per-invocation execution record attached to every tool dispatch.

    Attributes:
        attempt_id: UUID for this specific invocation attempt.
        tool_name: Registered tool name (possibly namespaced).
        tool_call_id: Provider-assigned call ID.
        input_fingerprint: Short hash of the serialised tool arguments.
        start_ts: Monotonic start time (seconds).
        end_ts: Monotonic end time (seconds, 0 if not yet complete).
        latency_ms: Rounded latency in milliseconds.
        is_error: Whether the result was an error.
        error_class: Exception class name on failure (empty string on success).
        recoverable: Whether the failure is considered transient.
        content_length: Byte length of the raw result content.
    """

    attempt_id: str
    tool_name: str
    tool_call_id: str
    input_fingerprint: str
    start_ts: float
    end_ts: float = 0.0
    latency_ms: int = 0
    is_error: bool = False
    error_class: str = ""
    recoverable: bool = True
    content_length: int = 0

    def close(
        self, *, content: str, is_error: bool, exc: Optional[Exception] = None
    ) -> None:
        self.end_ts = time.monotonic()
        self.latency_ms = int((self.end_ts - self.start_ts) * 1000)
        self.is_error = is_error
        self.content_length = len(content)
        if exc:
            self.error_class = type(exc).__name__
            # Non-recoverable if the error looks permanent
            msg = str(exc).lower()
            _perm = ("permission denied", "not found", "invalid api key", "unsupported")
            self.recoverable = not any(m in msg for m in _perm)


# ---------------------------------------------------------------------------
# Skill-level observability envelope
# ---------------------------------------------------------------------------


@dataclass
class SkillActivationEnvelope:
    """Per-skill-activation record capturing lifecycle and aggregate metrics.

    Opened when a skill is activated (activate_skill) and closed when it is
    unregistered or the loop ends.  Aggregates tool-level envelopes to
    compute success rate and latency totals.

    Attributes:
        skill_name: Name of the activated skill.
        activated_at_iteration: Loop iteration when the skill was activated.
        activated_at_ts: Monotonic timestamp at activation.
        closed_at_ts: Monotonic timestamp at close (0 if not yet closed).
        duration_ms: Rounded wall-clock duration in milliseconds.
        tool_count: Number of tool envelopes attributed to this skill.
        tool_success_rate: Ratio of successful tool calls (None if no calls).
        total_tool_latency_ms: Sum of latency across attributed tool envelopes.
        was_completed: True if the skill finished all steps (not abandoned).
        termination_reason: Short string describing why the skill ended
            (e.g. "completed", "unregistered", "abandoned", "budget_exhausted").
        preflight_warnings: Count of preflight warnings at activation time.
    """

    skill_name: str
    activated_at_iteration: int = 0
    activated_at_ts: float = 0.0
    closed_at_ts: float = 0.0
    duration_ms: int = 0
    tool_count: int = 0
    tool_success_rate: Optional[float] = None
    total_tool_latency_ms: int = 0
    was_completed: bool = False
    termination_reason: str = "abandoned"
    preflight_warnings: int = 0


def _input_fingerprint(arguments: str) -> str:
    """4-byte hex fingerprint of serialised arguments."""
    return hashlib.blake2b((arguments or "").encode(), digest_size=4).hexdigest()


# Per-directory threading locks for sys.path manipulation (3.7).
# Prevents a concurrent-import race when multiple tools from the same directory
# are imported simultaneously: one task must finish inserting+removing before
# another starts, so the path entry is never removed while a sibling import is
# still in flight.  Dict is created at module scope (no async needed here).
_syspath_dir_locks: Dict[str, threading.Lock] = {}
_syspath_dir_locks_lock = threading.Lock()


def _get_syspath_dir_lock(directory: str) -> threading.Lock:
    with _syspath_dir_locks_lock:
        if directory not in _syspath_dir_locks:
            _syspath_dir_locks[directory] = threading.Lock()
        return _syspath_dir_locks[directory]


@contextlib.contextmanager
def _syspath_containing_tool_file(tool_file: str):
    """Prepend the tool file's directory for the duration of import.

    Matches ``python scripts/foo.py``, which puts ``scripts`` on ``sys.path`` so
    sibling modules (e.g. ``from core import ...``) resolve.

    A per-directory threading lock is held for the duration of the context
    so that concurrent callers from the same directory do not race on
    sys.path.remove() (3.7).
    """
    from pathlib import Path

    parent = str(Path(tool_file).resolve().parent)
    lock = _get_syspath_dir_lock(parent)
    with lock:
        inserted = False
        if parent not in sys.path:
            sys.path.insert(0, parent)
            inserted = True
        try:
            yield
        finally:
            if inserted:
                try:
                    sys.path.remove(parent)
                except ValueError:
                    pass


class ToolDispatchError(Exception):
    """Raised when a tool dispatch fails."""


class ToolExecutor:
    """Dispatches tool calls from LLM responses to actual executors.

    ToolExecutor is NOT an Action -- it is a runtime helper instantiated by
    SkillInteractAction during execute(). It aggregates available tools
    from MCP servers and local handlers, validates calls, and dispatches
    them with timeout and error sanitization.

    Args:
        call_timeout: Timeout in seconds for each individual tool call.
        validate_calls: If True, validate tool calls before dispatch.
        max_concurrent_calls: Maximum concurrent tool executions.
        sanitize_errors: If True, replace internal error details with
            generic messages before returning to the LLM.
    """

    def __init__(
        self,
        call_timeout: float = 60.0,
        validate_calls: bool = True,
        max_concurrent_calls: int = 5,
        sanitize_errors: bool = True,
        allowed_tool_paths: Optional[List[str]] = None,
    ) -> None:
        self._tool_manager = ToolManager()
        self._handlers: Dict[str, Tuple[str, Any]] = {}  # name -> (kind, handler)
        self._registry = ToolRegistry()
        self._skill_bundles: Dict[str, Dict[str, Any]] = {}
        self._active_skill_bundles: Dict[str, Dict[str, Any]] = {}
        self.call_timeout = call_timeout
        self.max_concurrent_calls = max_concurrent_calls
        self.validate_calls = validate_calls
        self.sanitize_errors = sanitize_errors
        self._allowed_tool_paths: List[str] = allowed_tool_paths or []
        # Visitor from initialize(); used when dispatch() is called without an explicit visitor
        # (skill tools need action_resolver on the visitor).
        self._dispatch_visitor: Optional[Any] = None
        # Observability: all envelopes produced during this executor's lifetime
        self.envelopes: List[ToolExecutionEnvelope] = []
        self.skill_envelopes: List[SkillActivationEnvelope] = []
        # Skill chaining: named data handoff between skills (P2-10).
        self.skill_context: Dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Observability helpers
    # ------------------------------------------------------------------

    def success_rate(self) -> Optional[float]:
        """Return ratio of successful dispatches, or None if no calls made."""
        if not self.envelopes:
            return None
        successes = sum(1 for e in self.envelopes if not e.is_error)
        return successes / len(self.envelopes)

    def repeated_call_signatures(self) -> Dict[str, int]:
        """Return tool names with their call counts (>1 = potential stuck loop)."""
        from collections import Counter

        counts: Counter = Counter(e.tool_name for e in self.envelopes)
        return {name: cnt for name, cnt in counts.items() if cnt > 1}

    def total_latency_ms(self) -> int:
        """Sum of latency_ms across all envelopes."""
        return sum(e.latency_ms for e in self.envelopes)

    # ------------------------------------------------------------------
    # Skill-level observability
    # ------------------------------------------------------------------

    def open_skill_envelope(
        self, skill_name: str, *, preflight_warnings: int = 0
    ) -> SkillActivationEnvelope:
        """Open a skill activation envelope and return it.

        The caller may update ``activated_at_iteration`` after the fact if
        the loop iteration is known at the call site.
        """
        envelope = SkillActivationEnvelope(
            skill_name=skill_name,
            activated_at_ts=time.monotonic(),
            preflight_warnings=preflight_warnings,
        )
        self.skill_envelopes.append(envelope)
        return envelope

    def close_skill_envelope(
        self,
        skill_name: str,
        *,
        was_completed: bool = False,
        termination_reason: str = "abandoned",
    ) -> Optional[SkillActivationEnvelope]:
        """Close the envelope for *skill_name*, aggregating tool-level metrics.

        When the skill completed successfully, declared exports are extracted
        from the skill's tool results and placed into ``skill_context`` for
        downstream chained skills to import.

        Returns the closed envelope, or None if no open envelope was found.
        """
        for env in self.skill_envelopes:
            if env.skill_name == skill_name and env.closed_at_ts == 0.0:
                env.closed_at_ts = time.monotonic()
                env.duration_ms = int((env.closed_at_ts - env.activated_at_ts) * 1000)
                env.was_completed = was_completed
                env.termination_reason = termination_reason

                # Aggregate tool envelopes belonging to this skill
                prefix = f"{skill_name.replace('-', '_')}__"
                skill_tools = [
                    e for e in self.envelopes if e.tool_name.startswith(prefix)
                ]
                env.tool_count = len(skill_tools)
                if skill_tools:
                    successes = sum(1 for e in skill_tools if not e.is_error)
                    env.tool_success_rate = successes / len(skill_tools)
                    env.total_tool_latency_ms = sum(e.latency_ms for e in skill_tools)

                # Extract exports into skill_context when completed (P2-10)
                if was_completed:
                    self._extract_skill_exports(skill_name)
                return env
        return None

    def _validate_skill_imports(self, skill_name: str) -> List[str]:
        """Check declared imports against available ``skill_context``.

        Returns a list of missing key names (empty list = all satisfied).
        """
        bundle = self._skill_bundles.get(skill_name, {})
        imports: List[str] = bundle.get("imports", [])
        missing: List[str] = []
        for key in imports:
            if key not in self.skill_context:
                missing.append(key)
        return missing

    def _extract_skill_exports(self, skill_name: str) -> None:
        """Scan tool results for keys matching declared exports.

        Matches export names against JSON keys and content substrings from
        tool results belonging to the skill.  Populates ``skill_context``.
        """
        bundle = self._skill_bundles.get(skill_name, {})
        exports: List[str] = bundle.get("exports", [])
        if not exports:
            return
        prefix = f"{skill_name.replace('-', '_')}__"
        for name in exports:
            for env in reversed(self.envelopes):
                if not env.tool_name.startswith(prefix):
                    continue
                # The raw content was already consumed — store the name for
                # downstream skills to reference.  The actual values are in
                # the conversation context / evidence log.
                if name not in self.skill_context:
                    self.skill_context[name] = {
                        "source_skill": skill_name,
                        "source_tool": env.tool_name,
                    }

    def close_all_skill_envelopes(
        self, *, was_completed: bool = False, termination_reason: str = "abandoned"
    ) -> List[SkillActivationEnvelope]:
        """Close every still-open skill envelope.  Called at loop end."""
        closed: List[SkillActivationEnvelope] = []
        for env in self.skill_envelopes:
            if env.closed_at_ts == 0.0:
                closed.append(
                    self.close_skill_envelope(
                        env.skill_name,
                        was_completed=was_completed,
                        termination_reason=termination_reason,
                    )
                    or env
                )
        return closed

    def skill_activation_aggregates(self) -> Dict[str, Any]:
        """Return aggregate metrics across all skill activations."""
        if not self.skill_envelopes:
            return {"total_activations": 0}
        return {
            "total_activations": len(self.skill_envelopes),
            "completed": sum(1 for e in self.skill_envelopes if e.was_completed),
            "abandoned": sum(1 for e in self.skill_envelopes if not e.was_completed),
            "total_tool_calls": sum(e.tool_count for e in self.skill_envelopes),
            "overall_success_rate": (
                sum(
                    (e.tool_success_rate or 0.0) * e.tool_count
                    for e in self.skill_envelopes
                )
                / max(sum(e.tool_count for e in self.skill_envelopes), 1)
            ),
            "total_skill_duration_ms": sum(e.duration_ms for e in self.skill_envelopes),
        }

    @property
    def activated_skills(self) -> Set[str]:
        """Set of activated skill keys (hyphens normalized to underscores).

        Matches the prefix segment of namespaced skill tools (e.g. ``foo_bar__tool``).
        """
        return set(self._active_skill_bundles.keys())

    def record_skill_iteration(self, skill_name: str) -> None:
        """Increment the iteration counter for an active skill."""
        state = self._active_skill_bundles.get(skill_name)
        if state is not None:
            state["iterations"] = state.get("iterations", 0) + 1

    def skill_iteration_count(self, skill_name: str) -> int:
        """Return the number of iterations consumed by a skill."""
        state = self._active_skill_bundles.get(skill_name)
        return state.get("iterations", 0) if state else 0

    def check_skill_budget_exhausted(
        self,
        skill_name: str,
        max_iterations: int,
        max_duration_seconds: float,
    ) -> Optional[str]:
        """Check if a skill has exceeded its per-skill budget.

        Returns an error message if the budget is exhausted, or None if the
        skill can continue.
        """
        if max_iterations <= 0 and max_duration_seconds <= 0:
            return None
        state = self._active_skill_bundles.get(skill_name)
        if state is None:
            return None
        if max_iterations > 0:
            used = state.get("iterations", 0)
            if used >= max_iterations:
                return (
                    f"Skill '{skill_name}' has used its maximum of "
                    f"{max_iterations} iteration(s). Complete or skip remaining "
                    f"steps and move on."
                )
        if max_duration_seconds > 0:
            started = state.get("started_at")
            if started is not None:
                elapsed = time.monotonic() - started
                if elapsed >= max_duration_seconds:
                    return (
                        f"Skill '{skill_name}' has exceeded its time budget of "
                        f"{max_duration_seconds:.0f}s (elapsed: {elapsed:.0f}s). "
                        f"Complete or skip remaining steps and move on."
                    )
        return None

    async def initialize(
        self,
        visitor: Any,
        tool_servers: Optional[List[str]] = None,
        allowed_tool_patterns: Optional[List[str]] = None,
        denied_tool_patterns: Optional[List[str]] = None,
        local_tools_paths: Optional[List[str]] = None,
    ) -> None:
        """Discover and register all available tools.

        1. Find MCPAction instances by server_name from the agent graph
        2. Call list_tools() on each MCP client -> register as ToolDefinition
        3. Register any local Python tool handlers
        4. If local_tools_path provided, scan directory for tools
        5. Apply allowed/denied tool pattern filters
        6. Build ToolManager with all registered tools

        Args:
            visitor: The InteractWalker (provides agent access).
            tool_servers: Names of MCPAction instances to use.
            allowed_tool_patterns: Glob patterns for tool name to allow.
            denied_tool_patterns: Glob patterns for tool name to deny.
            local_tools_paths: Optional list of folders to scan for .py tools.
        """
        self._dispatch_visitor = visitor
        tool_servers = tool_servers or []

        # Register MCP tools
        for server_name in tool_servers:
            try:
                await self._register_mcp_server(visitor, server_name)
            except Exception as e:
                logger.warning(
                    "ToolExecutor: failed to register MCP server '%s': %s",
                    server_name,
                    e,
                    exc_info=True,
                )

        # Register tools from local directories
        if local_tools_paths:
            for path in local_tools_paths:
                try:
                    self._discover_local_tools(path)
                except Exception as e:
                    logger.error(
                        "ToolExecutor: failed to discover tools in '%s': %s",
                        path,
                        e,
                        exc_info=True,
                    )

        # Apply pattern filters
        if allowed_tool_patterns or denied_tool_patterns:
            self._apply_pattern_filters(allowed_tool_patterns, denied_tool_patterns)

        logger.info(
            "ToolExecutor initialized with %d tools: %s",
            len(self._tool_manager.tools),
            list(self._tool_manager.tools.keys()),
        )

    def _register_tool_with_registry(
        self,
        *,
        name: str,
        description: str,
        parameters: Dict[str, Any],
        source: str,
        handler_kind: str,
        handler: Any,
        fq_name: Optional[str] = None,
        prefix: Optional[str] = None,
    ) -> str:
        """Register a tool across registry, ToolManager, and dispatch handlers."""
        handle = self._registry.register(
            name=name,
            source=source,
            schema=parameters if isinstance(parameters, dict) else {},
            dispatch=handler,
            fq_name=fq_name,
            prefix=prefix,
        )
        self._tool_manager.register_tool(
            name=handle.name,
            description=description,
            parameters=parameters,
        )
        self._handlers[handle.name] = (handler_kind, handler)
        return handle.name

    async def _register_mcp_server(self, visitor: Any, server_name: str) -> None:
        """Register all tools from an MCP server.

        Args:
            visitor: The InteractWalker.
            server_name: Logical name of the MCPAction instance.
        """
        from jvagent.action.mcp.mcp_action import MCPAction

        agent = getattr(visitor, "_agent", None)
        if agent is None:
            logger.warning(
                "ToolExecutor: no agent on visitor, cannot find MCP server '%s'",
                server_name,
            )
            return

        # Find an MCPAction hosting this server_name — iterate all agent actions
        # (Agent doesn't have get_action() with filter support)
        mcp_action = None
        try:
            all_actions = await agent.get_actions()
            for action in all_actions:
                if not isinstance(action, MCPAction):
                    continue
                if server_name in action.get_server_names():
                    mcp_action = action
                    break
        except Exception as e:
            logger.warning("ToolExecutor: error getting actions from agent: %s", e)
            return

        if not mcp_action:
            logger.warning(
                "ToolExecutor: MCPAction with server_name '%s' not found", server_name
            )
            return

        if not getattr(mcp_action, "enabled", True):
            logger.debug(
                "ToolExecutor: MCPAction '%s' is disabled, skipping", server_name
            )
            return

        # Access the client and tools directly, bypassing fulfill()
        tools = await mcp_action.get_tools_cached(server_name)

        for mcp_tool in tools:
            name = getattr(mcp_tool, "name", "") or ""
            description = getattr(mcp_tool, "description", "") or ""
            input_schema = (
                getattr(mcp_tool, "input_schema", None)
                or getattr(mcp_tool, "inputSchema", None)
                or {"type": "object", "properties": {}}
            )
            try:
                registered_name = self._register_tool_with_registry(
                    name=name,
                    description=description,
                    parameters=(
                        input_schema
                        if isinstance(input_schema, dict)
                        else {"type": "object", "properties": {}}
                    ),
                    source="mcp",
                    handler_kind="mcp",
                    handler=(mcp_action, server_name),
                    fq_name=f"mcp:{server_name}:{name}",
                    prefix=f"mcp_{server_name}",
                )
                logger.debug(
                    "ToolExecutor: registered MCP tool '%s' as '%s'",
                    name,
                    registered_name,
                )
            except ValueError as e:
                logger.warning("ToolExecutor: skipping MCP tool '%s': %s", name, e)

    def _discover_local_tools(self, path: str) -> None:
        """Scan a directory for .py files and register them as tools.

        Expected format in each .py file:
        - get_tool_definition() -> dict: Returns OpenAI-format tool definition.
        - execute(arguments: dict) -> Any: Async function implementing the tool.

        Args:
            path: Absolute path to the tools directory.
        """
        if not os.path.isdir(path):
            logger.warning(
                "ToolExecutor: local_tools_path '%s' is not a directory", path
            )
            return

        logger.info("ToolExecutor: scanning for local tools in '%s'", path)

        for filename in os.listdir(path):
            if filename.startswith("_") or not filename.endswith(".py"):
                continue

            name = filename[:-3]  # Remove .py
            file_path = os.path.join(path, filename)

            try:
                # Dynamic import
                spec = importlib.util.spec_from_file_location(name, file_path)
                if not spec or not spec.loader:
                    continue

                module = importlib.util.module_from_spec(spec)
                sys.modules[name] = module
                with _syspath_containing_tool_file(file_path):
                    spec.loader.exec_module(module)

                # Check for required functions
                get_def = getattr(module, "get_tool_definition", None)
                handler = getattr(module, "execute", None)

                if not get_def or not handler:
                    logger.debug(
                        "ToolExecutor: file '%s' missing get_tool_definition or execute",
                        filename,
                    )
                    continue

                tool_def_dict = get_def()
                if not isinstance(tool_def_dict, dict):
                    logger.warning(
                        "ToolExecutor: %s.get_tool_definition() did not return dict",
                        name,
                    )
                    continue
                tool_name = tool_def_dict.get("function", {}).get("name")
                if not tool_name:
                    tool_name = tool_def_dict.get("name")

                if not tool_name:
                    logger.warning("ToolExecutor: tool in %s missing name", filename)
                    continue

                self.register_dynamic_tool(tool_name, tool_def_dict, handler)

            except Exception as e:
                logger.error(
                    "ToolExecutor: failed to load tool from '%s': %s",
                    filename,
                    e,
                    exc_info=True,
                )

    def register_dynamic_tool(
        self, name: str, tool_def_dict: Dict[str, Any], handler: Callable
    ) -> None:
        """Register a dynamic functional tool (like a locally discovered or runtime-generated tool)."""
        description = tool_def_dict.get("function", {}).get(
            "description"
        ) or tool_def_dict.get("description", "")
        parameters = tool_def_dict.get("function", {}).get(
            "parameters"
        ) or tool_def_dict.get("parameters", {"type": "object", "properties": {}})

        try:
            registered_name = self._register_tool_with_registry(
                name=name,
                description=description,
                parameters=parameters,
                source="dynamic",
                handler_kind="local",
                handler=handler,
                fq_name=f"dynamic:{name}",
                prefix="dynamic",
            )
            logger.debug(
                "ToolExecutor: registered dynamic tool '%s' as '%s'",
                name,
                registered_name,
            )
        except ValueError as e:
            logger.warning("ToolExecutor: skipping dynamic tool '%s': %s", name, e)

    def register_skill_bundle(
        self,
        skill_name: str,
        dir_path: str,
        tool_files: Optional[List[str]] = None,
        allowed_tools: Optional[List[str]] = None,
        exports: Optional[List[str]] = None,
        imports: Optional[List[str]] = None,
    ) -> None:
        """Register metadata for a skill bundle without exposing its tools yet."""
        self._skill_bundles[skill_name] = {
            "dir_path": dir_path,
            "tool_files": list(tool_files or []),
            "allowed_tools": set(allowed_tools or []),
            "exports": list(exports or []),
            "imports": list(imports or []),
        }

    async def activate_skill(
        self,
        skill_name: str,
        action_resolver: Optional[Any] = None,
        visitor: Optional[Any] = None,
    ) -> List[str]:
        """Load and register tool modules for a skill bundle on demand.

        Individual tools may declare ``requires_actions`` in their definition
        dict (top-level key).  Tools whose requirements cannot be satisfied
        are skipped with a warning — the rest of the skill remains available.

        Args:
            skill_name: The skill to activate.
            action_resolver: For validating tool-level ``requires_actions``.
            visitor: Optional interact-walker context threaded to loaded tool
                modules via ``_jvagent_visitor`` on the module.
        """
        bundle = self._skill_bundles.get(skill_name)
        if not bundle:
            raise ToolDispatchError(f"Skill bundle '{skill_name}' is not registered")

        # Tool names use underscores in the prefix; keep active-bundle keys aligned
        # so budget checks, unregister, and tool-prefix extraction stay consistent.
        safe_skill_name = skill_name.replace("-", "_")
        if safe_skill_name in self._active_skill_bundles:
            return []

        dir_path = str(bundle.get("dir_path") or "").strip()
        if dir_path and dir_path not in self._allowed_tool_paths:
            self._allowed_tool_paths.append(dir_path)

        # Ensure a parent package exists in sys.modules to enable relative imports
        # e.g. "jvagent_skill_appointment_booking"
        package_name = f"jvagent_skill_{safe_skill_name}"
        if package_name not in sys.modules:
            parent_module = importlib.util.module_from_spec(
                importlib.util.spec_from_loader(package_name, None)
            )
            parent_module.__path__ = [dir_path]
            sys.modules[package_name] = parent_module

        # First, scan and load ALL non-tool .py files in the directory to satisfy dependencies
        # (like _config.py)
        if dir_path and os.path.isdir(dir_path):
            for filename in os.listdir(dir_path):
                if filename.startswith("__") or not filename.endswith(".py"):
                    continue
                stem = filename[:-3]
                full_mod_name = f"{package_name}.{stem}"
                if full_mod_name in sys.modules:
                    continue
                # Skip tool files for this pass (they are loaded below)
                if filename in bundle.get("tool_files", []):
                    continue

                file_path = os.path.join(dir_path, filename)
                try:
                    spec = importlib.util.spec_from_file_location(
                        full_mod_name, file_path
                    )
                    if spec and spec.loader:
                        mod = importlib.util.module_from_spec(spec)
                        mod.__package__ = package_name
                        sys.modules[full_mod_name] = mod
                        spec.loader.exec_module(mod)
                except Exception as e:
                    logger.warning(
                        "ToolExecutor: failed to pre-load dependency '%s': %s",
                        filename,
                        e,
                    )

        registered: List[str] = []
        allowed_tools: Set[str] = bundle.get("allowed_tools", set())
        for file_path in bundle.get("tool_files", []):
            stem = Path(file_path).stem
            full_mod_name = f"{package_name}.{stem}"
            loaded_tool = await self._load_dynamic_tool_from_file(
                file_path=file_path,
                full_module_name=full_mod_name,
                package_name=package_name,
                allowed_tools=allowed_tools if allowed_tools else None,
                tool_name_prefix=safe_skill_name,
                action_resolver=action_resolver,
                visitor=visitor,
            )
            if loaded_tool:
                registered.append(loaded_tool)

        self._active_skill_bundles[safe_skill_name] = {
            "iterations": 0,
            "started_at": time.monotonic(),
        }
        self.open_skill_envelope(skill_name)
        # Validate chaining imports (P2-10)
        _import_warnings = self._validate_skill_imports(skill_name)
        if _import_warnings:
            logger.warning(
                "ToolExecutor: skill '%s' missing imports: %s",
                skill_name,
                _import_warnings,
            )
        return registered

    def _apply_pattern_filters(
        self,
        allowed_patterns: Optional[List[str]] = None,
        denied_patterns: Optional[List[str]] = None,
    ) -> None:
        """Filter registered tools by allowed/denied glob patterns."""
        tool_names = set(self._tool_manager.tools.keys())

        if allowed_patterns:
            allowed = set()
            for pattern in allowed_patterns:
                allowed.update(fnmatch.filter(tool_names, pattern))
            tool_names = tool_names & allowed

        if denied_patterns:
            denied = set()
            for pattern in denied_patterns:
                denied.update(fnmatch.filter(tool_names, pattern))
            tool_names = tool_names - denied

        # Remove tools not in filtered set
        removed = set(self._tool_manager.tools.keys()) - tool_names
        for name in removed:
            del self._tool_manager.tools[name]
            self._handlers.pop(name, None)
            self._registry.remove(name)

    def register_local_tool(
        self,
        name: str,
        handler: Callable,
        description: str,
        parameters: Dict[str, Any],
    ) -> ToolDefinition:
        """Register a local Python function as a tool.

        Args:
            name: Tool name (alphanumeric + underscores).
            handler: Async callable implementing the tool.
            description: Human-readable description.
            parameters: JSON Schema for parameters.

        Returns:
            The registered ToolDefinition.
        """
        registered_name = self._register_tool_with_registry(
            name=name,
            description=description,
            parameters=parameters,
            source="local",
            handler_kind="local",
            handler=handler,
            fq_name=f"local:{name}",
            prefix="local",
        )
        tool_def = self._tool_manager.get_tool(registered_name)
        if tool_def is None:
            raise ValueError(f"Failed to register local tool '{name}'")
        return tool_def

    async def dispatch(
        self, tool_calls: List[Dict[str, Any]], visitor: Any = None
    ) -> List[Dict[str, Any]]:
        """Dispatch a batch of tool calls and return tool result messages.

        Each tool call is validated, executed with a timeout, and the result
        is formatted as a tool result message ready to append to the
        conversation for the next LLM iteration.

        Args:
            tool_calls: List of raw tool call dicts from ModelActionResult.
            visitor: Optional InteractWalker for context. When omitted, uses the visitor
                passed to initialize() (required for skill tools that need ActionResolver).

        Returns:
            List of tool result messages in the format:
            [{"role": "tool", "tool_call_id": str, "content": str}]
        """
        parsed_calls = self._tool_manager.parse_tool_calls(tool_calls)
        effective_visitor = visitor if visitor is not None else self._dispatch_visitor

        # Execute concurrently with limit
        semaphore = asyncio.Semaphore(self.max_concurrent_calls)

        async def _dispatch_one(call: ToolCall) -> Dict[str, Any]:
            async with semaphore:
                return await self._dispatch_single(call, visitor=effective_visitor)

        results = await asyncio.gather(
            *[_dispatch_one(call) for call in parsed_calls],
            return_exceptions=False,
        )
        return list(results)

    async def _dispatch_single(
        self, call: ToolCall, visitor: Any = None
    ) -> Dict[str, Any]:
        """Dispatch a single tool call with validation, timeout, and observability.

        Args:
            call: The ToolCall to dispatch.
            visitor: Optional InteractWalker for context.

        Returns:
            Tool result message dict.
        """
        # Build observability envelope
        raw_args = ""
        if hasattr(call, "arguments"):
            import json as _json

            raw_args = (
                _json.dumps(call.arguments)
                if isinstance(call.arguments, dict)
                else str(call.arguments or "")
            )
        envelope = ToolExecutionEnvelope(
            attempt_id=uuid.uuid4().hex[:8],
            tool_name=call.name,
            tool_call_id=call.id or "",
            input_fingerprint=_input_fingerprint(raw_args),
            start_ts=time.monotonic(),
        )
        self.envelopes.append(envelope)

        # Validate
        if self.validate_calls:
            is_valid, error = self._tool_manager.validate_tool_call(call)
            if not is_valid:
                err_msg = error or "Validation failed"
                envelope.close(content=err_msg, is_error=True)
                return self._make_error_result(call.id, err_msg)

        # Find handler
        handler_entry = self._handlers.get(call.name)
        if handler_entry is None:
            available = list(self._tool_manager.tools.keys())
            err_msg = (
                f"Tool '{call.name}' is not available. Available tools: {available}"
            )
            envelope.close(content=err_msg, is_error=True)
            return self._make_error_result(call.id, err_msg)

        kind, handler = handler_entry

        try:
            if kind == "mcp":
                result_text = await asyncio.wait_for(
                    self._dispatch_mcp_tool(call, handler, visitor=visitor),
                    timeout=self.call_timeout,
                )
            elif kind == "local":
                result_text = await asyncio.wait_for(
                    self._dispatch_local_tool(call, handler, visitor=visitor),
                    timeout=self.call_timeout,
                )
            else:
                err_msg = f"Unknown handler kind: {kind}"
                envelope.close(content=err_msg, is_error=True)
                return self._make_error_result(call.id, err_msg)

            if isinstance(result_text, str) and not result_text.strip():
                result_text = f"Tool `{call.name}` returned empty output."

            envelope.close(content=result_text, is_error=False)
            logger.debug(
                "ToolExecutor: %s completed in %dms", call.name, envelope.latency_ms
            )
            return {
                "role": "tool",
                "tool_call_id": call.id,
                "content": result_text,
            }

        except asyncio.TimeoutError as te:
            err_msg = f"Tool call timed out after {self.call_timeout}s"
            envelope.close(content=err_msg, is_error=True, exc=te)
            return self._make_error_result(call.id, err_msg)
        except Exception as e:
            logger.warning("ToolExecutor: %s failed: %s", call.name, e)
            envelope.close(content=str(e), is_error=True, exc=e)
            if self.sanitize_errors:
                return self._make_error_result(
                    call.id, f"Tool execution failed: {call.name}"
                )
            return self._make_error_result(call.id, str(e))

    async def _dispatch_mcp_tool(
        self, call: ToolCall, mcp_handler: Any, visitor: Any = None
    ) -> str:
        """Execute a tool call against an MCP server.

        Calls MCPClientWrapper.call_tool() directly, bypassing fulfill().

        Args:
            call: The ToolCall.
            mcp_handler: Tuple of (MCPAction instance, server_name).
            visitor: Optional InteractWalker; used for per-user MCP sandboxing.

        Returns:
            Tool result as string.
        """
        mcp_action, server_name = mcp_handler
        # Per-user sandbox routing: prefer authenticated user_id, fall back
        # to session_id so anonymous visitors still land in their own folder
        # (rather than the shared system default). ``effective_user_segment``
        # centralizes the priority so file storage and MCP dispatch agree.
        from jvagent.action.mcp.sandbox import effective_user_segment

        if visitor is not None:
            uid_or_session = effective_user_segment(
                getattr(visitor, "user_id", None),
                getattr(visitor, "session_id", None),
                default="",
            )
            user_id = uid_or_session or None
        else:
            user_id = None
        gcfu = getattr(mcp_action, "get_client_for_user", None)

        if gcfu is not None and inspect.iscoroutinefunction(gcfu):
            try:
                client = await mcp_action.get_client_for_user(server_name, user_id)
            except Exception as e:
                logger.warning(
                    "ToolExecutor: get_client_for_user failed, using default client: %s",
                    e,
                )
                client = mcp_action.get_client(server_name)
        else:
            client = mcp_action.get_client(server_name)
        raw_tool_name = call.name
        handle = self._registry.get(call.name)
        if handle and handle.source == "mcp":
            fq = handle.fq_name.split(":")
            if len(fq) >= 3:
                raw_tool_name = fq[-1]

        call_result = await client.call_tool(raw_tool_name, call.arguments)
        normalized = _normalize_call_result(call_result, raw_tool_name)
        if normalized.is_error and normalized.text:
            raise ToolDispatchError(normalized.text)
        return normalized.text

    async def _dispatch_local_tool(
        self, call: ToolCall, handler: Callable, visitor: Any = None
    ) -> str:
        """Execute a local Python tool call.

        Args:
            call: The ToolCall.
            handler: The async callable.
            visitor: Optional InteractWalker for context.

        Returns:
            Tool result as string.
        """
        import inspect

        # Check if handler accepts visitor
        sig = inspect.signature(handler)
        maybe_result: Any
        if "visitor" in sig.parameters:
            maybe_result = handler(call.arguments, visitor=visitor)
        else:
            maybe_result = handler(call.arguments)

        result = (
            await maybe_result if inspect.isawaitable(maybe_result) else maybe_result
        )

        if isinstance(result, str):
            return result
        import json

        return json.dumps(result) if not isinstance(result, str) else result

    def _make_error_result(self, tool_call_id: str, message: str) -> Dict[str, Any]:
        """Create a tool error result message.

        Args:
            tool_call_id: The tool call ID.
            message: Error message.

        Returns:
            Tool result message dict with error content.
        """
        return {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": f"Error: {message}",
        }

    def get_tools_list(self) -> List[Dict[str, Any]]:
        """Return all registered tools in OpenAI function-calling format.

        Returns:
            List of tool definition dicts suitable for LLM query.
        """
        return self._tool_manager.get_tools_list()

    def get_tool_names(self) -> Set[str]:
        """Return the set of registered tool names."""
        return set(self._registry.names())

    def unregister_skill_bundle(self, skill_name: str) -> List[str]:
        """Remove a skill bundle and deregister its tools.

        Returns list of deregistered tool names.
        """
        safe_skill_name = skill_name.replace("-", "_")
        prefix = f"{safe_skill_name}__"
        deregistered: List[str] = []
        for name in list(self._handlers.keys()):
            if name.startswith(prefix):
                del self._handlers[name]
                self._registry.remove(name)
                if name in self._tool_manager.tools:
                    del self._tool_manager.tools[name]
                deregistered.append(name)

        self._skill_bundles.pop(skill_name, None)
        self._active_skill_bundles.pop(safe_skill_name, None)
        self.close_skill_envelope(skill_name, termination_reason="unregistered")
        if deregistered:
            logger.info(
                "ToolExecutor: unregistered skill '%s', removed tools: %s",
                skill_name,
                deregistered,
            )
        return deregistered

    async def cleanup(self) -> None:
        """Clean up resources after the loop completes."""
        self._handlers.clear()
        self._skill_bundles.clear()
        self._active_skill_bundles.clear()
        for name in list(self._registry.names()):
            self._registry.remove(name)
        self.envelopes.clear()
        self.skill_envelopes.clear()
        self.skill_context.clear()

    async def _load_dynamic_tool_from_file(
        self,
        file_path: str,
        full_module_name: str,
        package_name: Optional[str] = None,
        allowed_tools: Optional[Set[str]] = None,
        tool_name_prefix: Optional[str] = None,
        action_resolver: Optional[Any] = None,
        visitor: Optional[Any] = None,
    ) -> Optional[str]:
        """Import one local tool module and register it if valid.

        Tools may declare ``requires_actions`` in their definition dict
        (top-level key, outside ``function``).  If any declared action is
        unavailable the tool is skipped individually rather than failing the
        entire skill.

        The *visitor* is stored as ``_jvagent_visitor`` on the loaded module
        so skill tool modules can access walker context (conversation,
        action_resolver, per-user MCP sandboxing, etc.) without separate
        mechanisms.
        """
        source = Path(file_path).resolve()
        if not source.is_file():
            return None
        if source.name.startswith("_") or source.suffix != ".py":
            return None

        # Path validation: reject traversal and enforce allowed directories
        try:
            source.relative_to(Path.cwd().resolve())
        except ValueError:
            if self._allowed_tool_paths:
                allowed = any(
                    str(source).startswith(str(Path(p).resolve()))
                    for p in self._allowed_tool_paths
                )
                if not allowed:
                    logger.warning(
                        "ToolExecutor: rejected file outside allowed paths: %s",
                        source,
                    )
                    return None
            # If no allowed_tool_paths configured, allow cwd-relative only

        spec = importlib.util.spec_from_file_location(full_module_name, str(source))
        if not spec or not spec.loader:
            return None

        module = importlib.util.module_from_spec(spec)
        if package_name:
            module.__package__ = package_name
        sys.modules[full_module_name] = module
        with _syspath_containing_tool_file(str(source)):
            spec.loader.exec_module(module)

        get_def = getattr(module, "get_tool_definition", None)
        handler = getattr(module, "execute", None)
        if not get_def or not handler:
            return None

        # Thread visitor context to the loaded skill tool module (P2-9).
        if visitor is not None:
            setattr(module, "_jvagent_visitor", visitor)

        tool_def_dict = get_def()
        if not isinstance(tool_def_dict, dict):
            return None

        tool_name = tool_def_dict.get("function", {}).get("name") or tool_def_dict.get(
            "name"
        )
        if not tool_name:
            return None

        # ---- Tool-level requires_actions gating (P2-8) ----
        tool_requires: List[str] = tool_def_dict.get("requires_actions", [])
        if tool_requires and action_resolver:
            try:
                errors = await action_resolver.validate_requirements(tool_requires)
                if errors:
                    logger.warning(
                        "ToolExecutor: skipping tool '%s' — unmet requirements: %s",
                        tool_name,
                        errors,
                    )
                    return None
            except Exception as exc:
                logger.warning(
                    "ToolExecutor: skipping tool '%s' — validation error: %s",
                    tool_name,
                    exc,
                )
                return None

        # Check allowed_tools: match both bare and namespaced names
        if allowed_tools is not None:
            namespaced_name = (
                f"{tool_name_prefix}__{tool_name}" if tool_name_prefix else None
            )
            if tool_name not in allowed_tools and (
                namespaced_name is None or namespaced_name not in allowed_tools
            ):
                return None

        # Namespace tool name with skill prefix to avoid collisions
        # e.g. "search" from pageindex_search → "pageindex_search__search"
        registered_name = tool_name
        if tool_name_prefix:
            registered_name = f"{tool_name_prefix}__{tool_name}"
            # Update the tool definition so the LLM sees the namespaced name
            if "function" in tool_def_dict:
                tool_def_dict["function"]["name"] = registered_name
            else:
                tool_def_dict["name"] = registered_name

        self.register_dynamic_tool(registered_name, tool_def_dict, handler)
        return registered_name
