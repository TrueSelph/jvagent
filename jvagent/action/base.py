"""Base Action class for all pluggable actions in jvagent.

Actions are executable components that provide specific functionality to agents.
They can be enabled/disabled, have lifecycle hooks, maintain their own data
collections, and provide file storage capabilities.

Actions follow jvspatial's Node pattern since they are part of the agent graph
and have relationships with other components.
"""

import logging
import os
import traceback
from datetime import datetime
from typing import (
    TYPE_CHECKING,
    Any,
    ClassVar,
    Dict,
    List,
    Optional,
    Type,
    TypeVar,
    Union,
)

from jvspatial.core import Node
from jvspatial.core.annotations import attribute, compound_index

if TYPE_CHECKING:
    from jvagent.action.manifest import Manifest
    from jvagent.action.model.language.base import LanguageModelAction

logger = logging.getLogger(__name__)

T = TypeVar("T", bound="Action")


@compound_index([("agent_id", 1), ("enabled", 1)], name="agent_enabled")
@compound_index(
    [("agent_id", 1), ("label", 1)],
    name="agent_label",
    unique=True,
    partial_filter_expression={
        "context.agent_id": {"$gt": ""},
        "context.label": {"$gt": ""},
        "context.namespace": {"$gt": ""},
    },
)
class Action(Node):
    """Base action class for all action types.

    Represents an execution node on the agent action graph. Actions are executable
    components that provide specific functionality to agents. They can be enabled/disabled,
    have lifecycle hooks, maintain their own data collections, and provide file storage
    capabilities.

    This follows jvspatial's Node pattern since actions are part of the agent graph
    and have relationships with other components.

    Attributes:
        agent_id: ID of the agent this action belongs to
        namespace: Namespace for the action (organizes actions, prevents naming conflicts)
        label: Human-readable label for the action (used as identifier)
        description: Description of what the action does
        enabled: Whether the action is currently enabled
        metadata: Package metadata dictionary (from info.yaml)

    Configuration:
        All action-specific configuration should be defined as typed attributes
        on your Action subclass using the attribute standard. These can be overridden
        in agent.yaml using the property override mechanism.

        Example:
            class MyAction(Action):
                timeout: int = attribute(default=30, description="Operation timeout in seconds")
                retries: int = attribute(default=3, description="Number of retry attempts")
                api_url: str = attribute(default="https://api.example.com", description="API endpoint URL")

            # In agent.yaml:
            actions:
              - action: namespace/my_action
                context:
                  timeout: 60        # Override property
                  api_url: https://prod.api.example.com

    Lifecycle Hooks:
        on_register() - Called when action is set up for the first time
        on_reload() - Called when action is reloaded
        post_register() - Called after all actions are registered
        on_startup() - Called when app starts and action is loaded from database
        on_enable() - Called when action is enabled
        on_disable() - Called when action is disabled (action remains registered)
        on_deregister() - Called when action is deregistered (action is removed)
        healthcheck() - Called to perform health checks

    Action-to-Action Communication:
        Actions can retrieve other actions as tools using the get_action() method:

        # Get action by class type
        from jvagent.action.reply.reply_action import ReplyAction
        responder = await self.get_action(ReplyAction)

        # Get action by class name string
        llm = await self.get_action("OpenAILanguageModelAction")

        # Get LanguageModelAction (recommended for actions that need models)
        # Define model_action_type attribute to specify a particular model, or omit for any available
        llm = await self.get_model_action()  # Returns None if not found
        llm = await self.get_model_action(required=True)  # Raises error if not found

        # Get any instance of a base class (fallback)
        from jvagent.action.vectorstore.base import VectorStore
        vectorstore = await self.get_action(VectorStore)

    Note: Disabling an action (on_disable) does NOT deregister it. Deregistration
    (on_deregister) is a separate operation that removes the action from the system
    and automatically cleans up endpoints and modules.

    Child Nodes:
        Actions with attached child nodes (e.g., NewsSummaryCache) must connect
        them via outgoing edges. When an action is deleted, all child nodes
        reachable via outgoing edges are cascade-deleted.
    """

    # Core Attributes
    agent_id: str = attribute(
        indexed=True,
        protected=True,
        default="",
        description="ID of the agent this action belongs to",
    )
    enabled: bool = attribute(
        indexed=True,
        default=True,
        description="Whether the action is currently enabled",
    )
    namespace: str = attribute(
        default="", description="Namespace for the action (e.g., 'jvagent', 'contrib')"
    )
    label: str = attribute(
        indexed=True,
        default="",
        description="Human-readable label for the action (used as identifier)",
    )
    description: str = attribute(
        default="basic agent action", description="Description of what the action does"
    )
    metadata: Dict[str, Any] = attribute(
        default_factory=dict,
        description="Package metadata from info.yaml (name, version, config, etc.)",
    )
    module_path: str = attribute(
        indexed=True,
        default="",
        description=(
            "Canonical Python import path for this package (core_module_path or "
            "package module); used for indexed staleness checks vs filesystem."
        ),
    )
    # Scoped behavioural parameters — the common parameter subsystem (every
    # action carries these). Persona-shaped: each entry is
    # ``{condition?, response}`` plus a ``scope`` that routes WHERE it's applied
    # — ``orchestration`` (the agentic loop, under the Orchestrator) or
    # ``response`` (the response prompt, under the ReplyAction). The Orchestrator
    # accumulates every enabled action's parameters onto the interaction each
    # turn; each injection site renders only the params in its scope. Actions
    # natively declare their own core params (Orchestrator → orchestration,
    # Reply → response) and any action may contribute more. See
    # ``jvagent/action/parameters.py``.
    parameters: List[Dict[str, Any]] = attribute(
        default_factory=list,
        description=(
            "Scoped behavioural parameters this action contributes to the common "
            "subsystem. Each is {scope, condition?, response}: scope='orchestration' "
            "rules apply in the agentic loop, scope='response' rules in the reply "
            "compose. Accumulated onto the interaction and rendered by scope."
        ),
    )

    def get_capabilities(self) -> List[str]:
        """User-facing 'what the agent can do' statements this action contributes
        to the agent's advertised abilities.

        The orchestrator aggregates these across all enabled actions (and merges
        them with the available skill descriptions) to build the "WHAT YOU CAN
        DO" digest in its system prompt — so the model knows a capability exists
        and won't under-claim ("I can't sign you up…") even when the action's
        tool is lean-surfaced off the prompt.

        Default: none. Most actions are plumbing/egress and advertise nothing.
        An action that exposes a user-facing ability overrides this to furnish
        one or more short capability statements, e.g.::

            def get_capabilities(self) -> List[str]:
                return ["Sign users up for training (guided interview)"]
        """
        return []

    @staticmethod
    def canonical_import_module_path(metadata: Dict[str, Any]) -> str:
        """Derive persisted module_path from loader metadata (matches find_spec target)."""
        if not metadata:
            return ""
        if metadata.get("is_core_action"):
            return (metadata.get("core_module_path") or "").strip()
        return (metadata.get("module") or "").strip()

    def get_class_name(self) -> str:
        """Get the class name of this action.

        Returns the class name (e.g., "InteractRouter", "PersonaAction", "OpenAILanguageModelAction").
        This is a convenience method to avoid using __class__.__name__ throughout the codebase.

        Returns:
            Class name as a string
        """
        return self.__class__.__name__

    def get_capabilities(self) -> List[str]:
        """Return capabilities this action contributes to PersonaAction's prompt.

        Override this method to contribute capabilities to the persona when this action
        is enabled. PersonaAction aggregates these at runtime from all enabled actions.
        Returns empty list by default.

        Returns:
            List of capability strings (e.g., "Join WhatsApp groups and send messages")
        """
        return []

    async def get_responder(self) -> Optional["Action"]:
        """Resolve the agent's egress voice (ADR-0024/0025).

        ``ReplyAction`` is jvagent's single output contract — the one entity that
        gathers queued directives/parameters and voices one unified reply. It
        exposes ``reply``/``respond``/``publish``/``get_tools``. Returns ``None``
        when no ReplyAction is enabled (agents must enable one).
        """
        try:
            from jvagent.action.reply.reply_action import ReplyAction

            return await self.get_action(ReplyAction)
        except Exception as exc:
            logger.debug("get_responder: ReplyAction resolution failed: %s", exc)
            return None

    def get_manifest(self) -> "Manifest":
        """Return the pattern-agnostic :class:`Manifest` for this action.

        Resolution order (ADR-0010):

        1. ``self.metadata['manifest']`` from ``info.yaml`` (raw dict),
           parsed into a :class:`Manifest` via ``Manifest.from_payload``.
        2. ``agent.yaml`` ``context.manifest:`` shallow-merged on top
           (per-action overrides — handled when the agent loader writes
           into ``self.metadata['manifest']`` after merging contexts;
           reads happen here from a single source).
        3. Defaults from :class:`Manifest` (``latency_class="quick"``) when no
           manifest block was declared.

        The result is cached per-call; callers should treat it as
        immutable (it is a frozen dataclass). Override in subclasses only
        when an action needs to compute its manifest dynamically — most
        actions just declare the block in ``info.yaml``.
        """
        from jvagent.action.manifest import Manifest

        raw = (self.metadata or {}).get("manifest") if self.metadata else None
        return Manifest.from_payload(raw)

    async def get_tools(self) -> List[Any]:
        """Return Tool instances this action exposes for agentic-loop runs.

        Override in subclasses that provide executable tools callable by the
        language model. Each Tool wraps a named function with a JSON Schema
        for arguments.

        Returns:
            List of :class:`jvagent.tooling.tool.Tool` instances.
        """
        return []

    @property
    def config(self) -> Dict[str, Any]:
        """Get action configuration from metadata.

        Configuration is merged from info.yaml (package.config) and agent.yaml (config overrides).

        Returns:
            Configuration dictionary
        """
        base_config = self.metadata.get("config", {})
        # Config overrides from agent.yaml are stored separately in metadata
        overrides = self.metadata.get("config_overrides", {})
        # Merge: overrides take precedence
        merged = {**base_config, **overrides}
        return merged

    @property
    def is_singleton(self) -> bool:
        """True if this action type allows only one instance per agent."""
        return self.config.get("singleton", True)

    async def delete(self, cascade: bool = True) -> None:
        """Delete this action and cascade to child nodes.

        Actions with attached child nodes (e.g., NewsSummaryCache) must connect
        them via outgoing edges so cascade delete applies. This override
        explicitly deletes all outgoing child nodes before calling super().delete()
        as a safety net if jvspatial cascade misses any.
        """
        if cascade:
            try:
                # Get all nodes reachable via outgoing edges (child nodes)
                child_nodes = await self.nodes(direction="out")
                for child in child_nodes:
                    try:
                        await child.delete(cascade=True)
                    except Exception as e:
                        logger.warning(
                            f"Error cascade-deleting child node {getattr(child, 'id', child)} "
                            f"of action {self.id}: {e}"
                        )
            except Exception as e:
                logger.warning(
                    f"Error enumerating child nodes for action {self.id}: {e}"
                )

        await super().delete(cascade=cascade)

    # ============================================================================
    # Lifecycle Hooks
    # ============================================================================

    async def on_register(self) -> None:
        """Called when action is set up for the first time.

        Override this method to perform initialization tasks when the action
        is first registered. This is called before the action is enabled.

        Note: Errors in this method are automatically logged by the base system
        when called through enable() or the Actions manager. If you override this
        method, you can add additional error handling, but basic error logging
        is already provided.
        """
        pass

    async def on_reload(self) -> None:
        """Called when action is reloaded (e.g., after update).

        Override this method to handle reloading of action code, dependencies,
        or configuration. This is useful for hot-reloading actions during runtime.

        Note: Errors in this method are automatically logged by the base system
        when called through reload(). If you override this method, you can add
        additional error handling, but basic error logging is already provided.
        """
        pass

    async def post_register(self) -> None:
        """Called after all actions are registered.

        Override this method to perform tasks that require all actions to be
        registered first, such as resolving dependencies or setting up cross-action
        communication.

        Note: Errors in this method are automatically logged by the base system
        when called through the Actions manager. If you override this method, you
        can add additional error handling, but basic error logging is already provided.
        """
        pass

    async def on_enable(self) -> None:
        """Called when action is enabled.

        Override this method to perform tasks when the action transitions from
        disabled to enabled state, such as initializing connections or starting
        background tasks.

        Note: Errors in this method are automatically logged by the base system
        when called through enable(). If you override this method, you can add
        additional error handling, but basic error logging is already provided.
        """
        pass

    async def on_startup(self) -> None:
        """Called when app starts and action is loaded from database.

        Override this method to perform initialization tasks when the action
        is loaded on app startup. This is useful for re-initializing runtime
        components like channel adapters that don't persist across restarts.

        This hook is called for ALL loaded actions, regardless of their
        enabled state, but actions should check self.enabled if needed.

        Note: Errors in this method are automatically logged by the base system.
        """
        pass

    async def on_disable(self) -> None:
        """Called when action is disabled.

        Override this method to perform cleanup tasks when the action transitions
        from enabled to disabled state, such as closing connections or stopping
        background tasks.

        Note: Errors in this method are automatically logged by the base system
        when called through disable(). If you override this method, you can add
        additional error handling, but basic error logging is already provided.
        """
        pass

    async def on_deregister(self) -> None:
        """Called when action is deregistered from the Actions manager.

        Override this method to perform final cleanup tasks when the action is
        removed from the system.

        Note: This method is called automatically during deregistration, which also
        handles endpoint and module cleanup. Override only if you need additional
        action-specific cleanup.

        Errors in this method are automatically logged by the base system when
        called through the Actions manager. If you override this method, you
        can add additional error handling, but basic error logging is already provided.
        """
        pass

    # ============================================================================
    # Deregistration Cleanup Helpers
    # ============================================================================

    # ============================================================================
    # Endpoint cleanup contract (AUDIT-actions XC-4)
    # ============================================================================
    #
    # When an action is deregistered, ``_discover_action_endpoints`` finds
    # every endpoint whose path begins with ``/actions/{self.id}/`` so the
    # framework can unregister them.  Several first-party actions register
    # endpoints outside that prefix because the URL is externally pinned
    # (OAuth callback URLs in Cloud Console, webhook URLs registered with
    # WhatsApp/FB/Email providers) or because the path is admin-facing
    # (``/agents/{agent_id}/...``).
    #
    # Subclasses override the two class attrs below to declare extra
    # paths so deregister cleans them up:
    #
    #   ``additional_endpoint_path_prefixes``
    #     Literal path prefixes (no placeholders). Matched with
    #     ``str.startswith``.
    #
    #   ``additional_endpoint_path_templates``
    #     Templates with ``{action_id}`` / ``{agent_id}`` placeholders.
    #     Substituted with the action's own ids before matching.
    #
    # **Shared-route caveat**: paths shared across all instances of an
    # action class (e.g. ``/google/callback/`` is the dispatch entry for
    # EVERY GoogleAction instance) MUST NOT be declared here. Unregistering
    # them when one of N instances deregisters breaks the remaining N-1.
    # Only declare paths uniquely owned by ``self``.
    additional_endpoint_path_prefixes: ClassVar[List[str]] = []
    additional_endpoint_path_templates: ClassVar[List[str]] = []

    def _expand_endpoint_path_templates(self) -> List[str]:
        """Substitute ``{action_id}`` / ``{agent_id}`` placeholders.

        Returns the list of concrete prefixes derived from
        :attr:`additional_endpoint_path_templates` for this instance.
        """
        out: List[str] = []
        for tmpl in self.additional_endpoint_path_templates:
            try:
                out.append(tmpl.format(action_id=self.id, agent_id=self.agent_id))
            except (KeyError, IndexError):
                # Template referenced a placeholder we don't supply —
                # skip rather than crash deregister.
                continue
        return out

    def _discover_action_endpoints(self) -> List[Any]:
        """Discover all endpoints registered for this action.

        Queries the endpoint registry for endpoints matching this action's
        path patterns. The standard pattern is ``/actions/{action_id}/...``;
        subclasses can declare additional patterns via
        :attr:`additional_endpoint_path_prefixes` /
        :attr:`additional_endpoint_path_templates` (see AUDIT-actions XC-4).

        Returns:
            List of endpoint function callables to unregister.
        """
        try:
            from jvspatial.api.context import get_current_server

            server = get_current_server()
            if not server or not hasattr(server, "_endpoint_registry"):
                return []

            registry = server._endpoint_registry

            matching_prefixes: List[str] = [f"/actions/{self.id}/"]
            matching_prefixes.extend(self.additional_endpoint_path_prefixes)
            matching_prefixes.extend(self._expand_endpoint_path_templates())

            matching_endpoints = []
            seen_funcs: set = set()
            for func, endpoint_info in registry._function_registry.items():
                path = endpoint_info.path
                for prefix in matching_prefixes:
                    # Match either prefix-style (trailing slash, "starts
                    # with") or exact-path (no trailing slash, equality
                    # OR startswith of the path-with-slash form). Lets
                    # callers declare ``/google/callback/`` and also
                    # ``/google/{action_id}`` without ambiguity.
                    if path.startswith(prefix) or path == prefix.rstrip("/"):
                        if id(func) not in seen_funcs:
                            seen_funcs.add(id(func))
                            matching_endpoints.append(func)
                        break

            return matching_endpoints

        except Exception as e:
            logger.warning(f"Error discovering endpoints for action {self.id}: {e}")
            return []

    async def _unregister_endpoints(self) -> int:
        """Unregister all endpoints associated with this action.

        Discovers and unregisters all endpoints that match this action's path patterns.

        Returns:
            Number of endpoints successfully unregistered
        """
        try:
            from jvspatial.api.context import get_current_server

            server = get_current_server()
            if not server or not hasattr(server, "_endpoint_registry"):
                return 0

            registry = server._endpoint_registry

            # Discover endpoints for this action
            endpoints_to_unregister = self._discover_action_endpoints()

            if not endpoints_to_unregister:
                return 0

            # Unregister each endpoint
            unregistered_count = 0
            for endpoint_func in endpoints_to_unregister:
                try:
                    if registry.unregister_function(endpoint_func):
                        unregistered_count += 1
                except Exception as e:
                    import logging

                    logger = logging.getLogger(__name__)
                    logger.warning(
                        f"Error unregistering endpoint {endpoint_func.__name__}: {e}"
                    )

            # Also try unregistering by path pattern as a fallback for any remaining endpoints
            action_path_prefix = f"/actions/{self.id}/"
            try:
                # Get all function endpoints and check for any we might have missed
                for func, endpoint_info in registry._function_registry.items():
                    path = endpoint_info.path
                    if (
                        path.startswith(action_path_prefix)
                        and func not in endpoints_to_unregister
                    ):
                        # Found an endpoint we missed, try to unregister it
                        if registry.unregister_function(func):
                            unregistered_count += 1
            except Exception as exc:
                # Fallback sweep failed, but we already unregistered the tracked
                # endpoints above — log so a leaked route is diagnosable.
                logger.debug("endpoint unregister fallback failed: %s", exc)

            if unregistered_count > 0:
                import logging

                logger = logging.getLogger(__name__)
                logger.debug(
                    f"Unregistered {unregistered_count} endpoint(s) for action {self.id}"
                )

            return unregistered_count

        except Exception as e:
            import logging

            logger = logging.getLogger(__name__)
            logger.warning(f"Error unregistering endpoints for action {self.id}: {e}")
            return 0

    async def _unload_action_modules(self) -> int:
        """Unload modules that were loaded for this action.

        Safely removes modules from sys.modules if they are not:
        - Core jvagent modules
        - Imported by other actions
        - Shared dependencies

        Returns:
            Number of modules successfully unloaded
        """
        try:
            import logging
            import sys

            logger = logging.getLogger(__name__)

            # Get list of loaded modules from metadata
            if not hasattr(self, "metadata") or not self.metadata:
                return 0

            loaded_modules = self.metadata.get("loaded_modules", [])
            if not loaded_modules:
                return 0

            # Safety checks: don't unload core modules or shared dependencies
            core_module_prefixes = [
                "jvagent.action.",  # Core action modules (shared)
                "jvspatial.",  # jvspatial library (shared)
            ]

            unloaded_count = 0

            for module_name in loaded_modules:
                try:
                    # Skip core modules
                    if any(
                        module_name.startswith(prefix)
                        for prefix in core_module_prefixes
                    ):
                        logger.debug(f"Skipping core module: {module_name}")
                        continue

                    # Skip if module not in sys.modules
                    if module_name not in sys.modules:
                        continue

                    # Check if this is a local action module (jvagent.actions.*)
                    if module_name.startswith("jvagent.actions."):
                        # Local action modules can be safely unloaded
                        # But check if other actions might be using it
                        # For now, we'll be conservative and only unload if it's clearly this action's module
                        action_module_pattern = f"jvagent.actions.{self.metadata.get('namespace', '')}.{self.metadata.get('name', '')}"
                        if module_name.startswith(action_module_pattern):
                            # This is this action's specific module, safe to unload
                            del sys.modules[module_name]
                            unloaded_count += 1
                            logger.debug(f"Unloaded module: {module_name}")
                        else:
                            logger.debug(f"Skipping shared module: {module_name}")
                    else:
                        # Non-action modules - be very conservative
                        logger.debug(f"Skipping non-action module: {module_name}")

                except Exception as e:
                    logger.warning(f"Error unloading module {module_name}: {e}")
                    continue

            if unloaded_count > 0:
                logger.debug(
                    f"Unloaded {unloaded_count} module(s) for action {self.id}"
                )

            return unloaded_count

        except Exception as e:
            import logging

            logger = logging.getLogger(__name__)
            logger.warning(f"Error unloading modules for action {self.id}: {e}")
            return 0

    async def pulse(self) -> None:
        """Called periodically for maintenance operations.

        Override this method to perform periodic tasks such as health checks,
        cache cleanup, or background processing. The frequency is determined
        by the Actions manager.
        """
        pass

    async def healthcheck(self) -> Union[bool, Dict[str, Any]]:
        """Perform health check for this action.

        Override this method to implement action-specific health checks.
        Return True if healthy, False if unhealthy, or a dict with detailed
        health information.

        Returns:
            True if healthy, False if unhealthy, or dict with health details
        """
        return self.enabled

    # ============================================================================
    # Lifecycle Management
    # ============================================================================

    async def enable(self) -> None:
        """Enable this action.

        Calls the on_enable() lifecycle hook and updates the enabled state.
        This is the primary method for enabling an action.

        Errors from on_enable() are automatically logged to the database by the base system.
        """
        if not self.enabled:
            try:
                await self.on_enable()
                self.enabled = True
                await self.save()
            except Exception as e:
                # Log to console (database logging handled automatically by DBLogHandler)
                logger.error(
                    f"Error enabling {self.get_class_name()}: {e}",
                    exc_info=True,
                    details={
                        "agent_id": self.agent_id,
                        "action_class": self.get_class_name(),
                        "action_id": self.id,
                        "action_label": self.label,
                        "context": "on_enable",
                        "error_code": "action_enable_error",
                    },
                )
                raise

    async def disable(self) -> None:
        """Disable this action.

        Calls the on_disable() lifecycle hook and updates the enabled state.
        This is the primary method for disabling an action.

        Errors from on_disable() are automatically logged to the database by the base system.
        """
        if self.enabled:
            try:
                await self.on_disable()
                self.enabled = False
                await self.save()
            except Exception as e:
                # Log to console (database logging handled automatically by DBLogHandler)
                logger.error(
                    f"Error disabling {self.get_class_name()}: {e}",
                    exc_info=True,
                    details={
                        "agent_id": self.agent_id,
                        "action_class": self.get_class_name(),
                        "action_id": self.id,
                        "action_label": self.label,
                        "context": "on_disable",
                        "error_code": "action_disable_error",
                    },
                )
                raise

    async def reload(self) -> None:
        """Reload this action.

        Calls the on_reload() lifecycle hook. Useful for hot-reloading
        action code or configuration.

        Errors from on_reload() are automatically logged to the database by the base system.
        """
        try:
            await self.on_reload()
        except Exception as e:
            # Log to console (database logging handled automatically by DBLogHandler)
            logger.error(
                f"Error reloading {self.get_class_name()}: {e}",
                exc_info=True,
                details={
                    "agent_id": self.agent_id,
                    "action_class": self.get_class_name(),
                    "action_id": self.id,
                    "action_label": self.label,
                    "context": "on_reload",
                    "error_code": "action_reload_error",
                },
            )
            raise

    async def post_update(self) -> None:
        """Called after update operations.

        Override this method to perform tasks after the action has been updated,
        such as reinitializing connections or refreshing caches.
        """
        pass

    # ============================================================================
    # Graph Navigation
    # ============================================================================

    async def get_agent(self) -> Optional[Node]:
        """Get the agent node this action belongs to.

        Returns:
            Agent node if found, None otherwise
        """
        if not self.agent_id:
            return None

        from jvagent.core.agent import Agent

        try:
            return await Agent.get(self.agent_id)
        except Exception:
            return None

    async def get_app(self) -> Optional[Any]:
        """Get the App node for app-level operations.

        Returns:
            App node if found, None otherwise
        """
        from jvagent.core.app import App

        return await App.get()

    async def now(self, fmt: Optional[str] = None) -> Union[datetime, str]:
        """Current datetime in app timezone (or server local if App unavailable).

        Delegates to App.now(); falls back to datetime.now() if App unavailable.

        Args:
            fmt: Optional strftime format; if provided, returns formatted string.

        Returns:
            datetime object if fmt is None, else formatted string.
        """
        from jvagent.core.app import App

        app = await App.get()
        if app:
            return await app.now(fmt=fmt)
        now_dt = datetime.now()
        return now_dt.strftime(fmt) if fmt else now_dt

    async def get_action(
        self,
        action_class: Union[Type[T], str],
        enabled_only: bool = True,
    ) -> Optional[T]:
        """Get an action by exact class type or class name string (cached index lookup).

        For exact-type lookups this is O(1): it consults a cached
        ``class_name -> action_id`` index maintained at register/deregister time.

        **To search by base class** (e.g. "any LanguageModelAction"), use
        :meth:`get_action_by_base_class` — that method does an isinstance scan
        whose cost scales with the number of actions.

        Args:
            action_class: A class type or class-name string.
            enabled_only: If True, only return enabled actions (default: True).

        Returns:
            Action instance if found, None otherwise.
        """
        agent = await self.get_agent()
        if not agent:
            return None

        class_name: str = (
            action_class if isinstance(action_class, str) else action_class.__name__
        )

        from jvagent.core.cache import get_cached_action_id_by_type

        action_id = await get_cached_action_id_by_type(agent.id, class_name)
        if action_id:
            action = await Action.get(action_id)
            if action and isinstance(
                action, action_class if not isinstance(action_class, str) else Action
            ):
                if not enabled_only or action.enabled:
                    return action  # type: ignore[return-value]

        action = await agent.get_action_by_type(class_name)
        if action:
            if isinstance(
                action, action_class if not isinstance(action_class, str) else Action
            ):
                if not enabled_only or action.enabled:
                    # Repopulate type index on cache miss so subsequent lookups are O(1).
                    from jvagent.core.cache import cache_action_type_index

                    await cache_action_type_index(
                        agent.id, action.get_class_name(), action.id
                    )
                    return action  # type: ignore[return-value]

        return None

    async def get_action_by_base_class(
        self,
        base_class: Type[T],
        enabled_only: bool = True,
    ) -> Optional[T]:
        """Find any action that is an instance of *base_class* (isinstance scan).

        Unlike :meth:`get_action` this loads all actions and scans by type.
        The cost is O(n) in the number of actions. Use for base-class queries
        such as "any LanguageModelAction" or "any VectorStore".

        Args:
            base_class: Base class to match (subclasses included).
            enabled_only: If True, only return enabled actions.

        Returns:
            Action instance if found, None otherwise.
        """
        agent = await self.get_agent()
        if not agent:
            return None

        actions_manager = await agent.get_actions_manager()
        if actions_manager:
            all_actions = await actions_manager.get_actions(enabled_only=enabled_only)
            for action in all_actions:
                if isinstance(action, base_class):
                    return action  # type: ignore[return-value]
        return None

    async def get_model_action(
        self,
        required: bool = False,
    ) -> Optional["LanguageModelAction"]:
        """Get a LanguageModelAction for LLM calls.

        This is a convenience method for actions that need to use language models.
        Actions that require model usage should define a `model_action_type` attribute
        to specify a particular model action. If not specified, this method will
        fall back to finding any available LanguageModelAction.

        Args:
            required: If True, raises RuntimeError when no model action is found.
                     If False (default), returns None when not found.

        Returns:
            LanguageModelAction instance if found, None otherwise (unless required=True)

        Raises:
            RuntimeError: If required=True and no model action is found

        Examples:
            # Get model action (returns None if not found)
            model_action = await self.get_model_action()
            if model_action:
                response = await model_action.generate("Hello")

            # Require model action (raises error if not found)
            model_action = await self.get_model_action(required=True)
            response = await model_action.generate("Hello")
        """
        from jvagent.action.model.language.base import LanguageModelAction

        # Check if this action has a model_action_type attribute
        model_action_type = getattr(self, "model_action_type", None)

        # Try to get by type if specified
        if model_action_type:
            model_action = await self.get_action(model_action_type)
            if model_action and isinstance(model_action, LanguageModelAction):
                return model_action

        # Fallback: find first available LanguageModelAction
        model_action = await self.get_action(LanguageModelAction)
        if model_action:
            return model_action

        # Not found - raise error if required, otherwise return None
        if required:
            agent = await self.get_agent()
            agent_id = agent.id if agent else "unknown"
            model_type_str = model_action_type or "LanguageModelAction"
            raise RuntimeError(
                f"Model action of type '{model_type_str}' not found for agent '{agent_id}'"
            )

        return None

    # ============================================================================
    # Package Information
    # ============================================================================

    async def get_namespace(self) -> Optional[str]:
        """Get the namespace of the action package.

        Returns:
            Namespace string from package info, or None
        """
        return self.metadata.get("namespace")

    async def get_module(self) -> Optional[str]:
        """Get the module name of the action.

        Returns:
            Module name from package info, or None
        """
        return self.metadata.get("module")

    async def get_module_root(self) -> Optional[str]:
        """Get the root directory of the action module.

        Returns:
            Module root path from package info, or None
        """
        return self.metadata.get("module_root")

    async def get_package_path(self) -> Optional[str]:
        """Get the filesystem path to the action package.

        The package path is constructed as: /agents/{agent_name}/actions/{namespace}/{action_name}/

        Returns:
            Full path to the action package directory, or None if not available
        """
        if not self.agent_id or not self.label:
            return None

        # Get package name from metadata or use label
        package_name = self.metadata.get("name") or self.label

        # Get namespace (defaults to "default" if not set)
        namespace = self.namespace or "default"

        # Construct path: /agents/{agent_name}/actions/{namespace}/{action_name}/
        base_path = os.getenv("JVAGENT_BASE_PATH", ".")

        # Get agent name from metadata if available
        agent_name = self.metadata.get("agent_name")
        if not agent_name:
            # If not in metadata, we can't construct the path
            return None

        package_path = os.path.join(
            base_path, "agents", agent_name, "actions", namespace, package_name
        )

        # Return absolute path if relative
        if not os.path.isabs(package_path):
            package_path = os.path.abspath(package_path)

        return package_path if os.path.exists(package_path) else None

    async def get_version(self) -> str:
        """Get the version string of the action.

        Returns:
            Version string (from attribute or package info)
        """
        return self.metadata.get("version", "")

    async def get_package_name(self) -> Optional[str]:
        """Get the package name.

        Returns:
            Package name from package info, or label if not available
        """
        return self.metadata.get("name") or self.label

    def get_action_ref(self) -> Optional[str]:
        """Package ref ``namespace/action_name`` from loader metadata (info.yaml)."""
        from jvagent.scaffold.skill_resolve import action_ref_from_metadata

        return action_ref_from_metadata(self.metadata or {})

    async def resolve_skill_scan_dirs(
        self,
        *,
        include_legacy_agent_skills: bool = True,
    ) -> List[str]:
        """Filesystem directories to scan for action-backed skill packages.

        Uses this action's ``info.yaml`` identity (``namespace`` + ``name`` in
        metadata) and the hosting agent's tree — no per-action hardcoded refs.
        See ADR-0020 overlay layout: ``agents/.../actions/<ns>/<action>/skills/``.
        """
        from jvagent.scaffold.skill_resolve import resolve_action_skill_scan_dirs

        meta = self.metadata or {}
        app_root = None
        try:
            from jvagent.core.app_context import get_app_root

            app_root = get_app_root()
        except Exception:
            app_root = None

        agent_ns = meta.get("agent_namespace")
        agent_name = meta.get("agent_name")
        if not agent_ns or not agent_name:
            try:
                agent = await self.get_agent()
            except Exception:
                agent = None
            if agent is not None:
                agent_ns = agent_ns or getattr(agent, "namespace", None)
                agent_name = agent_name or getattr(agent, "name", None)

        return resolve_action_skill_scan_dirs(
            meta,
            app_root=app_root,
            agent_namespace=agent_ns,
            agent_name=agent_name,
            include_legacy_agent_skills=include_legacy_agent_skills,
        )

    async def get_type(self) -> str:
        """Get the type/category of the action.

        Returns:
            Action type from package info, or "generic" if not specified
        """
        return self.metadata.get("type", "generic")

    # ============================================================================
    # File Management
    # ============================================================================

    def _get_storage_path(self, path: str) -> str:
        """Get the storage path for a file relative to the action package.

        Constructs a path in the format: actions/{agent_id}/{package_name}/{path}

        Args:
            path: Relative path to the file within the package directory

        Returns:
            Full storage path for the file
        """
        # Reject absolute paths and parent-directory traversal so a caller-
        # supplied ``path`` cannot escape the action's storage prefix (parity
        # with the MCP sandbox / PageIndex / fileinterface path validation).
        rel = str(path or "").replace("\\", "/").strip()
        segments = [seg for seg in rel.split("/") if seg not in ("", ".")]
        if any(seg == ".." for seg in segments):
            raise ValueError(
                f"unsafe storage path (parent traversal not allowed): {path!r}"
            )
        rel = "/".join(segments)

        package_name = self.metadata.get("name") or self.label
        if not self.agent_id or not package_name:
            return rel

        # Construct storage path: actions/{agent_id}/{package_name}/{path}
        return os.path.join("actions", self.agent_id, package_name, rel).replace(
            "\\", "/"
        )

    async def get_file(self, path: str) -> Optional[bytes]:
        """Get a file from the action's package directory using App's file storage.

        Uses the App node's file storage operations to retrieve files. The file path
        is constructed as: actions/{agent_id}/{package_name}/{path}

        Args:
            path: Relative path to the file within the package directory

        Returns:
            File contents as bytes, or None if file not found or storage unavailable
        """
        from jvagent.core.app import App

        app = await App.get()
        if not app:
            return None

        storage_path = self._get_storage_path(path)
        return await app.get_file(storage_path)

    async def save_file(
        self, path: str, content: bytes, metadata: Optional[Dict[str, Any]] = None
    ) -> bool:
        """Save a file to the action's package directory using App's file storage.

        Uses the App node's file storage operations to save files. The file path
        is constructed as: actions/{agent_id}/{package_name}/{path}

        Args:
            path: Relative path to the file within the package directory
            content: File contents as bytes
            metadata: Optional file metadata (tags, user info, etc.)

        Returns:
            True if successful, False otherwise
        """
        from jvagent.core.app import App

        app = await App.get()
        if not app:
            return False

        storage_path = self._get_storage_path(path)
        return await app.save_file(storage_path, content, metadata=metadata)

    async def delete_file(self, path: str) -> bool:
        """Delete a file from the action's package directory using App's file storage.

        Uses the App node's file storage operations to delete files. The file path
        is constructed as: actions/{agent_id}/{package_name}/{path}

        Args:
            path: Relative path to the file within the package directory

        Returns:
            True if successful, False otherwise
        """
        from jvagent.core.app import App

        app = await App.get()
        if not app:
            return False

        storage_path = self._get_storage_path(path)
        return await app.delete_file(storage_path)

    async def get_file_url(self, path: str) -> Optional[str]:
        """Get a URL for accessing a file from the action's package.

        Uses the App node's file storage operations to generate URLs. For local storage,
        this returns a relative path. For S3 storage, this returns a signed URL.

        Args:
            path: Relative path to the file within the package directory

        Returns:
            URL string if available, None otherwise
        """
        from jvagent.core.app import App

        app = await App.get()
        if not app:
            return None

        storage_path = self._get_storage_path(path)
        return await app.get_file_url(storage_path)

    async def get_short_file_url(
        self,
        path: str,
        with_filename: bool = False,
        expires_in: int = 3600,
        one_time: bool = False,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        """Get a shortened URL for accessing a file using App's file storage proxy system.

        Uses the App node's file storage operations to create a short, secure URL for file access.
        The proxy URL can be configured with expiration and one-time use.

        Args:
            path: Relative path to the file within the package directory
            with_filename: Whether to include filename in the URL (not used for proxy URLs)
            expires_in: Expiration time in seconds (default: 3600 = 1 hour)
            one_time: Whether the URL should be one-time use only (default: False)
            metadata: Optional metadata to attach to the proxy (default: None)

        Returns:
            Shortened proxy URL string if available, None otherwise
        """
        from jvagent.core.app import App

        app = await App.get()
        if not app:
            return None

        storage_path = self._get_storage_path(path)

        # Add action info to metadata
        proxy_metadata = metadata or {}
        proxy_metadata.update(
            {
                "action_id": self.id,
                "action_label": self.label,
                "agent_id": self.agent_id,
            }
        )

        return await app.create_proxy_url(
            path=storage_path,
            expires_in=expires_in,
            one_time=one_time,
            metadata=proxy_metadata,
        )

    async def to_dict(self) -> Dict[str, Any]:
        """Convert action to dictionary representation.

        Returns:
            Dictionary containing action data (excluding private fields)
        """
        return {
            "id": self.id,
            "agent_id": self.agent_id,
            "namespace": self.namespace,
            "version": await self.get_version(),
            "label": self.label,
            "description": self.description,
            "enabled": self.enabled,
            "type": await self.get_type(),
            "package_name": await self.get_package_name(),
        }
