"""Base Action class for all pluggable actions in jvagent.

Actions are executable components that provide specific functionality to agents.
They can be enabled/disabled, have lifecycle hooks, maintain their own data
collections, and provide file storage capabilities.

Actions follow jvspatial's Node pattern since they are part of the agent graph
and have relationships with other components.
"""

import os
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Union

from jvspatial.api import endpoint
from jvspatial.api.endpoints.response import ResponseField, success_response
from jvspatial.api.exceptions import ResourceNotFoundError
from jvspatial.core import Node
from jvspatial.core.annotations import attribute
from jvspatial.core.pager import ObjectPager

if TYPE_CHECKING:
    pass  # App imported locally when needed


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
        _metadata: Package metadata dictionary (private, from info.yaml)

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
        on_enable() - Called when action is enabled
        on_disable() - Called when action is disabled
        on_deregister() - Called when action is deregistered
        healthcheck() - Called to perform health checks
    """

    # Core Attributes
    agent_id: str = attribute(
        protected=True, default="", description="ID of the agent this action belongs to"
    )
    enabled: bool = attribute(default=True, description="Whether the action is currently enabled")
    namespace: str = attribute(
        default="", description="Namespace for the action (e.g., 'jvagent', 'contrib')"
    )
    label: str = attribute(
        default="", description="Human-readable label for the action (used as identifier)"
    )
    description: str = attribute(
        default="basic agent action", description="Description of what the action does"
    )
    # Package metadata Information (private - loaded from info.yaml)
    _metadata: Dict[str, Any] = attribute(private=True, default_factory=dict)

    @property
    def config(self) -> Dict[str, Any]:
        """Get action configuration from metadata.

        Configuration is merged from info.yaml (package.config) and agent.yaml (config overrides).

        Returns:
            Configuration dictionary
        """
        base_config = self._metadata.get("config", {})
        # Config overrides from agent.yaml are stored separately in _metadata
        overrides = self._metadata.get("config_overrides", {})
        # Merge: overrides take precedence
        merged = {**base_config, **overrides}
        return merged

    # ============================================================================
    # Lifecycle Hooks
    # ============================================================================

    async def on_register(self) -> None:
        """Called when action is set up for the first time.

        Override this method to perform initialization tasks when the action
        is first registered. This is called before the action is enabled.
        """
        pass

    async def on_reload(self) -> None:
        """Called when action is reloaded (e.g., after update).

        Override this method to handle reloading of action code, dependencies,
        or configuration. This is useful for hot-reloading actions during runtime.
        """
        pass

    async def post_register(self) -> None:
        """Called after all actions are registered.

        Override this method to perform tasks that require all actions to be
        registered first, such as resolving dependencies or setting up cross-action
        communication.
        """
        pass

    async def on_enable(self) -> None:
        """Called when action is enabled.

        Override this method to perform tasks when the action transitions from
        disabled to enabled state, such as initializing connections or starting
        background tasks.
        """
        pass

    async def on_disable(self) -> None:
        """Called when action is disabled.

        Override this method to perform cleanup tasks when the action transitions
        from enabled to disabled state, such as closing connections or stopping
        background tasks.
        """
        pass

    async def on_deregister(self) -> None:
        """Called when action is deregistered from the Actions manager.

        Override this method to perform final cleanup tasks when the action is
        removed from the system.
        """
        pass

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
        """
        if not self.enabled:
            await self.on_enable()
            self.enabled = True
            await self.save()

    async def disable(self) -> None:
        """Disable this action.

        Calls the on_disable() lifecycle hook and updates the enabled state.
        This is the primary method for disabling an action.
        """
        if self.enabled:
            await self.on_disable()
            self.enabled = False
            await self.save()

    async def reload(self) -> None:
        """Reload this action.

        Calls the on_reload() lifecycle hook. Useful for hot-reloading
        action code or configuration.
        """
        await self.on_reload()

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

    async def get_collection(self) -> Optional[Node]:
        """Get the collection node associated with this action.

        Returns:
            Collection node if found, None otherwise
        """
        # TODO: Implement when Collection class is available
        # This will traverse the graph to find connected Collection nodes
        return None

    async def remove_collection(self) -> list:
        """Remove all collection nodes associated with this action.

        Returns:
            List of removed collection nodes
        """
        # TODO: Implement when Collection class is available
        return []

    # ============================================================================
    # Package Information
    # ============================================================================

    async def get_namespace(self) -> Optional[str]:
        """Get the namespace of the action package.

        Returns:
            Namespace string from package info, or None
        """
        return self._metadata.get("namespace")

    async def get_module(self) -> Optional[str]:
        """Get the module name of the action.

        Returns:
            Module name from package info, or None
        """
        return self._metadata.get("module")

    async def get_module_root(self) -> Optional[str]:
        """Get the root directory of the action module.

        Returns:
            Module root path from package info, or None
        """
        return self._metadata.get("module_root")

    async def get_package_path(self) -> Optional[str]:
        """Get the filesystem path to the action package.

        The package path is constructed as: /agents/{agent_name}/actions/{namespace}/{action_name}/

        Returns:
            Full path to the action package directory, or None if not available
        """
        if not self.agent_id or not self.label:
            return None

        # Get package name from metadata or use label
        package_name = self._metadata.get("name") or self.label

        # Get namespace (defaults to "default" if not set)
        namespace = self.namespace or "default"

        # Construct path: /agents/{agent_name}/actions/{namespace}/{action_name}/
        base_path = os.getenv("JVAGENT_BASE_PATH", ".")

        # Get agent name from metadata if available
        agent_name = self._metadata.get("agent_name")
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
        return self._metadata.get("version", "")

    async def get_package_name(self) -> Optional[str]:
        """Get the package name.

        Returns:
            Package name from package info, or label if not available
        """
        return self._metadata.get("name") or self.label

    async def get_type(self) -> str:
        """Get the type/category of the action.

        Returns:
            Action type from package info, or "generic" if not specified
        """
        return self._metadata.get("type", "generic")

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
        package_name = self._metadata.get("name") or self.label
        if not self.agent_id or not package_name:
            return path

        # Construct storage path: actions/{agent_id}/{package_name}/{path}
        return os.path.join("actions", self.agent_id, package_name, path).replace("\\", "/")

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
            path=storage_path, expires_in=expires_in, one_time=one_time, metadata=proxy_metadata
        )

    # ============================================================================
    # Data Management
    # ============================================================================

    async def export_collection(self) -> Dict[str, Any]:
        """Export collection data associated with this action.

        Returns:
            Dictionary containing exported collection data
        """
        # TODO: Implement when Collection class is available
        return {}

    async def import_collection(self, data: Dict[str, Any], purge: bool = True) -> bool:
        """Import collection data into this action.

        Args:
            data: Dictionary containing collection data to import
            purge: Whether to purge existing data before importing

        Returns:
            True if successful, False otherwise
        """
        # TODO: Implement when Collection class is available
        return False

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


# =============================================================================
# Action CRUD Endpoints (following jvspatial pattern of endpoints in class file)
# =============================================================================


@endpoint(
    "/actions/{action_id}",
    methods=["GET"],
    auth=True,
    tags=["Action"],
    response=success_response(
        data={
            "action": ResponseField(
                field_type=Dict[str, Any],
                description="Action information",
                example={
                    "id": "action_123",
                    "agent_id": "agent_456",
                    "namespace": "jvagent",
                    "label": "example_action",
                    "description": "Example action",
                    "enabled": True,
                },
            )
        }
    ),
)
async def get_action(action_id: str) -> Dict[str, Any]:
    """Get a specific action by ID.

    Args:
        action_id: ID of the action to retrieve

    Returns:
        Dictionary with action information

    Raises:
        ResourceNotFoundError: If action not found
    """
    action = await Action.get(action_id)
    if not action:
        raise ResourceNotFoundError(
            message=f"Action with ID '{action_id}' not found", details={"action_id": action_id}
        )

    return {"action": await action.export()}


@endpoint(
    "/actions/{action_id}",
    methods=["PUT"],
    auth=True,
    tags=["Action"],
    response=success_response(
        data={
            "action": ResponseField(
                field_type=Dict[str, Any],
                description="Updated action information",
            ),
            "message": ResponseField(
                field_type=str,
                description="Success message",
                example="Action updated successfully",
            ),
            "update_result": ResponseField(
                field_type=Dict[str, Any],
                description="Update operation result",
                example={
                    "success": True,
                    "updated": {"var_a": 60, "var_b": 10},
                    "skipped": {"invalid_field": "invalid_property"},
                    "message": "Partially updated: 2 succeeded, 1 skipped",
                },
            ),
        }
    ),
)
async def update_action(
    action_id: str,
    enabled: Optional[bool] = None,
    description: Optional[str] = None,
    properties: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Update an action.

    Uses Action.enable(), Action.disable() methods for lifecycle management.
    Custom properties can be updated via the properties parameter.

    Args:
        action_id: ID of the action to update
        enabled: Whether the action should be enabled
        description: New description
        properties: Dictionary of property names to values for runtime updates

    Returns:
        Dictionary with updated action information

    Raises:
        ResourceNotFoundError: If action not found

    Example Request Body:
        {
            "enabled": true,
            "description": "Updated action description",
            "properties": {
                "var_a": 60,
                "var_b": 10,
                "timeout": 45,
                "retries": 5
            }
        }

        Or update properties directly (for ExampleAction):
        {
            "var_a": 60,
            "var_b": 10
        }
    """
    action = await Action.get(action_id)
    if not action:
        raise ResourceNotFoundError(
            message=f"Action with ID '{action_id}' not found", details={"action_id": action_id}
        )

    # Track all updates
    updated_fields: Dict[str, Any] = {}
    skipped_fields: Dict[str, str] = {}
    needs_save = False

    # Update enabled status using Action methods
    if enabled is not None:
        if enabled != action.enabled:
            if enabled:
                await action.enable()
            else:
                await action.disable()
            updated_fields["enabled"] = enabled
            needs_save = True

    # Update description
    if description is not None:
        if description != action.description:
            action.description = description
            updated_fields["description"] = description
            needs_save = True

    # Update custom properties (runtime configuration changes)
    # Use entity-centric update() inherited from Object - works correctly for ExampleAction
    properties_result = None
    if properties:
        # Call update() directly on the entity instance
        # This inherits from Object.update() and correctly uses ExampleAction's class hierarchy
        properties_result = await action.update(properties, skip_protected=True, skip_private=True)

        # Merge properties update results
        if properties_result["updated"]:
            updated_fields.update(properties_result["updated"])
            needs_save = True

        if properties_result["skipped"]:
            skipped_fields.update(properties_result["skipped"])

            # Log any skipped properties
            import logging

            logger = logging.getLogger(__name__)
            for prop_name, reason in properties_result["skipped"].items():
                logger.warning(f"Could not update property '{prop_name}': {reason}")

    # Save if any updates were made and trigger lifecycle hooks
    if needs_save:
        await action.save()
        # Trigger reload hook if action is already registered
        await action.on_reload()

    # Build combined update result
    has_updates = len(updated_fields) > 0
    has_skipped = len(skipped_fields) > 0

    if has_updates and not has_skipped:
        message = f"Successfully updated {len(updated_fields)} field(s)"
    elif has_updates and has_skipped:
        message = (
            f"Partially updated: {len(updated_fields)} succeeded, {len(skipped_fields)} skipped"
        )
    elif has_skipped:
        message = f"Update failed: {len(skipped_fields)} field(s) skipped"
    else:
        message = "No changes to apply"

    update_result = {
        "success": has_updates,
        "updated": updated_fields,
        "skipped": skipped_fields,
        "message": message,
    }

    return {"action": await action.export(), "message": message, "update_result": update_result}


@endpoint(
    "/agents/{agent_id}/actions",
    methods=["GET"],
    auth=True,
    tags=["Action"],
    response=success_response(
        data={
            "actions": ResponseField(
                field_type=List[Dict[str, Any]],
                description="List of actions",
            ),
            "total": ResponseField(
                field_type=int,
                description="Total number of actions",
                example=100,
            ),
            "page": ResponseField(
                field_type=int,
                description="Current page number",
                example=1,
            ),
            "per_page": ResponseField(
                field_type=int,
                description="Number of actions per page",
                example=10,
            ),
            "total_pages": ResponseField(
                field_type=int,
                description="Total number of pages",
                example=10,
            ),
            "has_previous": ResponseField(
                field_type=bool,
                description="Whether there's a previous page",
                example=False,
            ),
            "has_next": ResponseField(
                field_type=bool,
                description="Whether there's a next page",
                example=True,
            ),
            "previous_page": ResponseField(
                field_type=Optional[int],  # type: ignore[arg-type]
                description="Previous page number",
                example=None,
            ),
            "next_page": ResponseField(
                field_type=Optional[int],  # type: ignore[arg-type]
                description="Next page number",
                example=2,
            ),
        }
    ),
)
async def list_agent_actions(
    agent_id: str,
    page: int = 1,
    per_page: int = 10,
    enabled_only: bool = False,
) -> Dict[str, Any]:
    """List actions for an agent using entity-centric pagination.

    Uses ObjectPager which automatically performs class-aware queries that include
    all Action subclasses (e.g., ExampleAction) through database-driven class discovery.
    This ensures dynamically loaded action classes are found even if not yet imported.

    Args:
        agent_id: ID of the agent
        page: Page number (1-based)
        per_page: Items per page
        enabled_only: Only return enabled actions

    Returns:
        Dictionary with paginated list of actions and pagination metadata
    """
    # Build entity-centric filters
    filters = {"context.agent_id": agent_id}
    if enabled_only:
        filters["context.enabled"] = True

    # ObjectPager uses _build_database_query_async with enable_class_discovery=True
    # This automatically discovers and includes all Action subclasses from the database
    pager = ObjectPager(Action, page_size=per_page, filters=filters)
    actions = await pager.get_page(page=page)

    # Convert to dicts
    actions_list = [await a.export() for a in actions]

    # Get pagination info
    pagination_info = pager.to_dict()

    return {
        "actions": actions_list,
        "total": pagination_info["total_items"],
        "page": pagination_info["current_page"],
        "per_page": pagination_info["page_size"],
        "total_pages": pagination_info["total_pages"],
        "has_previous": pagination_info["has_previous"],
        "has_next": pagination_info["has_next"],
        "previous_page": pagination_info["previous_page"],
        "next_page": pagination_info["next_page"],
    }


@endpoint(
    "/actions/{action_id}/enable",
    methods=["POST"],
    auth=True,
    tags=["Action"],
    response=success_response(
        data={
            "action": ResponseField(field_type=Dict[str, Any], description="Action information"),
            "message": ResponseField(field_type=str, description="Success message"),
        }
    ),
)
async def enable_action_endpoint(action_id: str) -> Dict[str, Any]:
    """Enable an action using Action.enable() method.

    Args:
        action_id: ID of the action to enable

    Returns:
        Dictionary with updated action information
    """
    action = await Action.get(action_id)
    if not action:
        raise ResourceNotFoundError(
            message=f"Action with ID '{action_id}' not found", details={"action_id": action_id}
        )

    await action.enable()

    return {
        "action": await action.to_dict(),
        "message": "Action enabled successfully",
    }


@endpoint(
    "/actions/{action_id}/disable",
    methods=["POST"],
    auth=True,
    tags=["Action"],
    response=success_response(
        data={
            "action": ResponseField(field_type=Dict[str, Any], description="Action information"),
            "message": ResponseField(field_type=str, description="Success message"),
        }
    ),
)
async def disable_action_endpoint(action_id: str) -> Dict[str, Any]:
    """Disable an action using Action.disable() method.

    Args:
        action_id: ID of the action to disable

    Returns:
        Dictionary with updated action information
    """
    action = await Action.get(action_id)
    if not action:
        raise ResourceNotFoundError(
            message=f"Action with ID '{action_id}' not found", details={"action_id": action_id}
        )

    await action.disable()

    return {
        "action": await action.to_dict(),
        "message": "Action disabled successfully",
    }


@endpoint(
    "/actions/{action_id}/reload",
    methods=["POST"],
    auth=True,
    tags=["Action"],
    response=success_response(
        data={
            "action": ResponseField(field_type=Dict[str, Any], description="Action information"),
            "message": ResponseField(field_type=str, description="Success message"),
        }
    ),
)
async def reload_action_endpoint(action_id: str) -> Dict[str, Any]:
    """Reload an action using Action.reload() method.

    Args:
        action_id: ID of the action to reload

    Returns:
        Dictionary with action information
    """
    action = await Action.get(action_id)
    if not action:
        raise ResourceNotFoundError(
            message=f"Action with ID '{action_id}' not found", details={"action_id": action_id}
        )

    await action.reload()

    return {
        "action": await action.to_dict(),
        "message": "Action reloaded successfully",
    }


@endpoint(
    "/actions/{action_id}/health",
    methods=["GET"],
    auth=True,
    tags=["Action"],
    response=success_response(
        data={
            "health": ResponseField(field_type=Dict[str, Any], description="Health information"),
        }
    ),
)
async def check_action_health(action_id: str) -> Dict[str, Any]:
    """Check action health using Action.healthcheck() method.

    Args:
        action_id: ID of the action to check

    Returns:
        Dictionary with health information
    """
    action = await Action.get(action_id)
    if not action:
        raise ResourceNotFoundError(
            message=f"Action with ID '{action_id}' not found", details={"action_id": action_id}
        )

    health = await action.healthcheck()

    # Normalize result
    if isinstance(health, bool):
        health = {"healthy": health}
    elif not isinstance(health, dict):
        health = {"healthy": True, "result": health}

    return {"health": health}
