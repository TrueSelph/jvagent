"""Base Action class for all pluggable actions in jvagent.

Actions are executable components that provide specific functionality to agents.
They can be enabled/disabled, have lifecycle hooks, maintain their own data
collections, and provide file storage capabilities.

Actions follow jvspatial's Node pattern since they are part of the agent graph
and have relationships with other components.
"""

import os
from typing import Any, Dict, Optional, Union

from jvspatial.core import Node
from jvspatial.core.annotations import attribute
from pydantic import Field

from jvagent.core.app import App


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
        version: Version string for the action
        label: Human-readable label for the action (used as identifier)
        description: Description of what the action does
        enabled: Whether the action is currently enabled
        _package: Package configuration dictionary (private, from info.yaml)
    
    Lifecycle Hooks:
        on_register() - Called when action is registered
        on_reload() - Called when action is reloaded
        post_register() - Called after all actions are registered
        on_enable() - Called when action is enabled
        on_disable() - Called when action is disabled
        on_deregister() - Called when action is deregistered
        pulse() - Called for periodic operations
        analytics() - Called to gather analytics data
    """
    
    # Core Attributes
    agent_id: str = Field(default="", description="ID of the agent this action belongs to")
    version: str = Field(default="", description="Version string for the action")
    label: str = Field(default="", description="Human-readable label for the action (used as identifier)")
    description: str = Field(default="basic agent action", description="Description of what the action does")
    enabled: bool = Field(default=True, description="Whether the action is currently enabled")
    
    # Package Information (private - loaded from info.yaml)
    _package: Dict[str, Any] = attribute(private=True, default_factory=dict)
    
    # ============================================================================
    # Lifecycle Hooks
    # ============================================================================
    
    async def on_register(self) -> None:
        """Called when action is registered with the Actions manager.
        
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
    
    async def analytics(self) -> Dict[str, Any]:
        """Gather analytics data for this action.
        
        Override this method to return analytics/metrics data specific to this
        action. This data is collected by the Actions manager for reporting.
        
        Returns:
            Dictionary containing analytics data
        """
        return {
            "enabled": self.enabled,
            "version": self.version,
            "label": self.label,
        }
    
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
    
    async def update(self, data: Optional[Dict[str, Any]] = None) -> "Action":
        """Update action with new data.
        
        This method updates the action's attributes and triggers on_reload()
        if the action is already registered.
        
        Args:
            data: Dictionary of attributes to update
            
        Returns:
            Self for method chaining
        """
        if data:
            for key, value in data.items():
                if hasattr(self, key) and not key.startswith("_"):
                    setattr(self, key, value)
            await self.save()
            # Trigger reload hook if action is already registered
            await self.on_reload()
        return self
    
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
        return self._package.get("namespace")
    
    async def get_module(self) -> Optional[str]:
        """Get the module name of the action.
        
        Returns:
            Module name from package info, or None
        """
        return self._package.get("module")
    
    async def get_module_root(self) -> Optional[str]:
        """Get the root directory of the action module.
        
        Returns:
            Module root path from package info, or None
        """
        return self._package.get("module_root")
    
    async def get_package_path(self) -> Optional[str]:
        """Get the filesystem path to the action package.
        
        The package path is constructed as: /actions/{agent_id}/{package_name}/
        
        Returns:
            Full path to the action package directory, or None if not available
        """
        if not self.agent_id or not self.label:
            return None
        
        # Get package name from package info or use label
        package_name = self._package.get("name") or self.label
        
        # Construct path: /actions/{agent_id}/{package_name}/
        # Note: This assumes actions are stored relative to the application root
        # The actual base path should be configurable
        base_path = os.getenv("JVAGENT_ACTIONS_PATH", "./actions")
        package_path = os.path.join(base_path, self.agent_id, package_name)
        
        # Return absolute path if relative
        if not os.path.isabs(package_path):
            # Try to resolve relative to current working directory or application root
            package_path = os.path.abspath(package_path)
        
        return package_path if os.path.exists(package_path) else None
    
    async def get_version(self) -> str:
        """Get the version string of the action.
        
        Returns:
            Version string (from attribute or package info)
        """
        return self.version or self._package.get("version", "")
    
    async def get_package_name(self) -> Optional[str]:
        """Get the package name.
        
        Returns:
            Package name from package info, or label if not available
        """
        return self._package.get("name") or self.label
    
    async def get_type(self) -> str:
        """Get the type/category of the action.
        
        Returns:
            Action type from package info, or "generic" if not specified
        """
        return self._package.get("type", "generic")
    
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
        package_name = self._package.get("name") or self.label
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
        app = await App.get()
        if not app:
            return None
        
        storage_path = self._get_storage_path(path)
        return await app.get_file(storage_path)
    
    async def save_file(self, path: str, content: bytes, metadata: Optional[Dict[str, Any]] = None) -> bool:
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
        metadata: Optional[Dict[str, Any]] = None
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
        app = await App.get()
        if not app:
            return None
        
        storage_path = self._get_storage_path(path)
        
        # Add action info to metadata
        proxy_metadata = metadata or {}
        proxy_metadata.update({
            "action_id": self.id,
            "action_label": self.label,
            "agent_id": self.agent_id,
        })
        
        return await app.create_proxy_url(
            path=storage_path,
            expires_in=expires_in,
            one_time=one_time,
            metadata=proxy_metadata
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
            "version": self.version,
            "label": self.label,
            "description": self.description,
            "enabled": self.enabled,
            "type": await self.get_type(),
            "package_name": await self.get_package_name(),
        }

