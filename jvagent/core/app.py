"""App node - Root application node for jvagent."""

import asyncio
import logging
import os
from datetime import datetime
from typing import Any, ClassVar, Dict, Optional, Type, Union

from jvspatial.api.context import get_current_server
from jvspatial.core import Node, Root
from jvspatial.core.annotations import attribute
from jvspatial.storage import create_storage, get_proxy_manager
from jvspatial.storage.exceptions import StorageError


class App(Node):
    """Root application node representing the jvagent application.

    This node serves as the root of the application graph and manages
    the overall system state, including application-wide services like
    file storage.

    Attributes:
        name: Application name
        version: Application version
        description: Application description
        status: Application status (active, inactive, maintenance)
        file_storage_provider: Storage provider type (local, s3, etc.)
        file_storage_root_dir: Root directory for local storage
        file_storage_enabled: Whether file storage is enabled
        _file_interface: File storage interface instance (private, transient)
        _proxy_manager: URL proxy manager instance (private, transient)
    """

    # Application metadata
    app_id: str = attribute(
        default="jvagent_app",
        description="Stable application identifier from app.yaml (app: key)",
    )
    name: str = "jvAgent"
    version: str = "0.0.1"
    description: str = "jvagent Application"
    status: str = "active"  # active, inactive, maintenance

    # File storage configuration
    file_storage_provider: str = attribute(
        default="local", description="Storage provider type (local, s3, etc.)"
    )
    file_storage_root_dir: str = attribute(
        default=".files", description="Root directory for local storage"
    )
    file_storage_enabled: bool = attribute(
        default=True, description="Whether file storage is enabled"
    )

    # Logging configuration
    logging_enabled: bool = attribute(
        default=True, description="Whether logging is enabled for this app"
    )
    log_retention_days: int = attribute(
        default=60, description="Log retention window in days (default: 60)"
    )

    timezone: Optional[str] = attribute(
        default=None,
        description="IANA timezone for app-level datetime (e.g. America/New_York)",
    )

    # Runtime instances (private, transient)
    _file_interface: Any = attribute(private=True, default=None)
    _proxy_manager: Any = attribute(private=True, default=None)

    # Class-level cache and lock for singleton access
    _cached_app: ClassVar[Optional["App"]] = None
    _lock: ClassVar[asyncio.Lock] = asyncio.Lock()

    # ============================================================================
    # Singleton Access
    # ============================================================================

    @classmethod
    async def get(cls: Type["App"]) -> Optional["App"]:
        """Get the App node from the graph, with caching.

        This method provides convenient singleton-like access to the App node.
        It traverses from Root -> App and caches the result for subsequent calls.

        Returns:
            App node if found, None otherwise

        Example:
            ```python
            app = await App.get()
            if app:
                file_content = await app.get_file("path/to/file")
            ```
        """
        # Return cached instance if available
        if cls._cached_app is not None:
            # Verify the cached instance still exists
            try:
                # Quick check - if it has an ID, assume it's still valid
                if cls._cached_app.id:
                    return cls._cached_app
            except Exception:
                # If verification fails, clear cache and re-fetch
                cls._cached_app = None

        # Use lock to prevent concurrent fetches
        async with cls._lock:
            # Double-check after acquiring lock
            if cls._cached_app is not None:
                try:
                    if cls._cached_app.id:
                        return cls._cached_app
                except Exception:
                    cls._cached_app = None

            # Get Root node
            root = await Root.get()
            if not root:
                return None

            # Get App node connected to Root
            app_nodes = await root.nodes()
            for node in app_nodes:
                if isinstance(node, App):
                    cls._cached_app = node
                    return node

            # App node not found
            return None

    @classmethod
    def clear_cache(cls) -> None:
        """Clear the cached App instance.

        This is useful when the App node might have been deleted or recreated,
        or when you want to force a fresh lookup on the next get() call.
        """
        cls._cached_app = None

    # ============================================================================
    # Datetime (App Timezone)
    # ============================================================================

    async def now(self, fmt: Optional[str] = None) -> Union[datetime, str]:
        """Current datetime in app timezone (or server local if unset).

        Args:
            fmt: Optional strftime format; if provided, returns formatted string.

        Returns:
            datetime object if fmt is None, else formatted string.
        """
        logger = logging.getLogger(__name__)
        tz = None
        if self.timezone:
            try:
                from zoneinfo import ZoneInfo

                tz = ZoneInfo(self.timezone)
            except Exception as e:
                logger.warning(
                    f"Invalid timezone '{self.timezone}', using server time: {e}"
                )
        now_dt = datetime.now(tz) if tz else datetime.now()
        if fmt:
            return now_dt.strftime(fmt)
        return now_dt

    # ============================================================================
    # Action Initialization
    # ============================================================================

    async def initialize_actions(self) -> Dict[str, bool]:
        """Initialize all actions by calling their on_startup() hooks.

        This method should be called when the app starts to ensure all actions
        are properly initialized, including their runtime components like
        channel adapters.

        Returns:
            Dict mapping action IDs to initialization status
        """
        import logging

        from jvagent.action.base import Action
        from jvagent.core.agent import Agent

        logger = logging.getLogger(__name__)
        results = {}

        try:
            # Get all agents via App -> Agents -> Agent path
            # Agents are connected to the Agents node, which is connected to App
            from jvagent.core.agents import Agents as AgentsNode

            agents_node = await AgentsNode.get()
            if not agents_node:
                return results

            agents = await agents_node.get_connected_agents()

            # For each agent, get all actions and call on_startup
            for agent in agents:
                actions_manager = await agent.get_actions_manager()
                if not actions_manager:
                    continue

                actions = await actions_manager.get_actions()

                for action in actions:
                    try:
                        await action.on_startup()
                        results[action.id] = True
                    except Exception as e:
                        logger.error(
                            f"Error in on_startup for {action.label}: {e}",
                            exc_info=True,
                        )
                        results[action.id] = False

            return results
        except Exception as e:
            logger.error(f"Error initializing actions: {e}", exc_info=True)
            return results

    # ============================================================================
    # File Storage Operations
    # ============================================================================

    async def get_file_interface(self):
        """Get or initialize the file storage interface.

        Tries to use server's configured storage first, then falls back to
        creating a storage instance based on this node's configuration.

        Returns:
            FileStorageInterface instance
        """
        # Return cached interface if available
        if self._file_interface:
            return self._file_interface

        # Try to get from server first (uses server's configuration)
        server = get_current_server()
        if server and hasattr(server, "_file_interface") and server._file_interface:
            self._file_interface = server._file_interface
            return self._file_interface

        # Resolve provider and root_dir: env > node > default
        env_provider = os.getenv("JVSPATIAL_FILE_INTERFACE", "").strip()
        provider = (
            env_provider if env_provider else (self.file_storage_provider or "local")
        )
        env_root = os.getenv("JVSPATIAL_FILES_ROOT_PATH", "").strip()
        root_dir = env_root if env_root else (self.file_storage_root_dir or ".files")

        # Create storage based on resolved configuration
        if provider == "local":
            self._file_interface = create_storage(provider="local", root_dir=root_dir)
        elif provider == "s3":
            self._file_interface = create_storage(
                provider="s3",
                bucket_name=os.getenv("JVSPATIAL_S3_BUCKET_NAME", ""),
                region_name=os.getenv("JVSPATIAL_S3_REGION_NAME", "us-east-1"),
                access_key_id=os.getenv("JVSPATIAL_S3_ACCESS_KEY_ID"),
                secret_access_key=os.getenv("JVSPATIAL_S3_SECRET_ACCESS_KEY"),
                endpoint_url=os.getenv("JVSPATIAL_S3_ENDPOINT_URL"),
            )
        else:
            self._file_interface = create_storage(provider=provider, root_dir=root_dir)

        return self._file_interface

    async def get_proxy_manager(self):
        """Get or initialize the URL proxy manager.

        Returns:
            URLProxyManager instance if available, None otherwise
        """
        # Return cached manager if available
        if self._proxy_manager:
            return self._proxy_manager

        # Try to get from server first
        server = get_current_server()
        if server and hasattr(server, "_proxy_manager") and server._proxy_manager:
            self._proxy_manager = server._proxy_manager
            return self._proxy_manager

        # Get proxy manager following jvspatial convention
        try:
            self._proxy_manager = get_proxy_manager()
        except Exception:
            self._proxy_manager = None

        return self._proxy_manager

    async def get_file(self, path: str) -> Optional[bytes]:
        """Get file content from storage.

        Args:
            path: Storage path to the file

        Returns:
            File content as bytes, or None if not found
        """
        if not self.file_storage_enabled:
            return None

        file_interface = await self.get_file_interface()
        if not file_interface:
            return None

        try:
            return await file_interface.get_file(path)
        except StorageError:
            return None
        except Exception:
            return None

    async def save_file(
        self, path: str, content: bytes, metadata: Optional[Dict[str, Any]] = None
    ) -> bool:
        """Save file to storage.

        Args:
            path: Storage path for the file
            content: File content as bytes
            metadata: Optional file metadata

        Returns:
            True if successful, False otherwise
        """
        if not self.file_storage_enabled:
            return False

        file_interface = await self.get_file_interface()
        if not file_interface:
            return False

        try:
            result = await file_interface.save_file(path, content, metadata=metadata)
            return result is not None
        except StorageError:
            return False
        except Exception:
            return False

    async def delete_file(self, path: str) -> bool:
        """Delete file from storage.

        Args:
            path: Storage path to the file

        Returns:
            True if successful, False otherwise
        """
        if not self.file_storage_enabled:
            return False

        file_interface = await self.get_file_interface()
        if not file_interface:
            return False

        try:
            return await file_interface.delete_file(path)
        except StorageError:
            return False
        except Exception:
            return False

    async def file_exists(self, path: str) -> bool:
        """Check if file exists in storage.

        Args:
            path: Storage path to the file

        Returns:
            True if file exists, False otherwise
        """
        if not self.file_storage_enabled:
            return False

        file_interface = await self.get_file_interface()
        if not file_interface:
            return False

        try:
            return await file_interface.file_exists(path)
        except StorageError:
            return False
        except Exception:
            return False

    async def get_file_url(self, path: str) -> Optional[str]:
        """Get URL for accessing a file.

        Args:
            path: Storage path to the file

        Returns:
            URL string if available, None otherwise
        """
        if not self.file_storage_enabled:
            return None

        file_interface = await self.get_file_interface()
        if not file_interface:
            return None

        try:
            if not await file_interface.file_exists(path):
                return None

            # Get metadata which may contain storage_url
            metadata = await file_interface.get_metadata(path)
            if metadata and "storage_url" in metadata:
                return metadata["storage_url"]

            # Use relative path
            return f"/api/storage/{path}"
        except StorageError:
            return None
        except Exception:
            return None

    async def create_proxy_url(
        self,
        path: str,
        expires_in: int = 3600,
        one_time: bool = False,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        """Create a proxy URL for secure file access.

        Args:
            path: Storage path to the file
            expires_in: Expiration time in seconds (default: 3600)
            one_time: Whether URL should be one-time use (default: False)
            metadata: Optional metadata to attach to proxy

        Returns:
            Proxy URL string if successful, None otherwise
        """
        if not self.file_storage_enabled:
            return None

        # Verify file exists first
        if not await self.file_exists(path):
            return None

        proxy_manager = await self.get_proxy_manager()
        if not proxy_manager:
            # Use regular file URL
            return await self.get_file_url(path)

        try:
            proxy = await proxy_manager.create_proxy(
                file_path=path,
                expires_in=expires_in,
                one_time=one_time,
                metadata=metadata,
            )

            if proxy and hasattr(proxy, "code"):
                return f"/p/{proxy.code}"
        except Exception:
            # Use regular file URL
            return await self.get_file_url(path)

        return None
