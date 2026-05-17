"""App node - Root application node for jvagent."""

import asyncio
import logging
import os
import threading
from datetime import datetime, timezone
from typing import Any, ClassVar, Dict, Optional, Type, Union

from jvspatial.api.constants import APIRoutes
from jvspatial.api.context import get_current_server
from jvspatial.core import Node, Root
from jvspatial.core.annotations import attribute
from jvspatial.core.context import get_default_context
from jvspatial.env import env, resolve_file_storage_root
from jvspatial.storage import create_storage, get_proxy_manager
from jvspatial.storage.exceptions import StorageError

logger = logging.getLogger(__name__)


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
        default="./.files", description="Root directory for local storage"
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

    # Next-start YAML sync intent: run | merge | source (protected from bulk/YAML overwrites;
    # mutate only via set_app_update_mode — see jvspatial AttributeMixin / Object.update).
    update_mode: str = attribute(
        default="run",
        protected=True,
        description=(
            "Stored bootstrap intent for next start: run (default), merge, or source. "
            "Maps to bootstrap YAML-sync when CLI omits --update."
        ),
    )

    # Runtime instances (private, transient)
    _file_interface: Any = attribute(private=True, default=None)
    _proxy_manager: Any = attribute(private=True, default=None)

    # Class-level cache for the singleton App node.
    _cached_app: ClassVar[Optional["App"]] = None
    # The lock is created lazily per running event loop because module-import
    # locks bind to whichever loop happens to be current at import time and
    # break on serverless warm starts where each invocation gets a fresh loop.
    # ``_locks`` maps ``id(loop) -> asyncio.Lock``; ``_locks_guard`` is a
    # threading lock so the dict access stays safe across worker threads.
    _locks: ClassVar[Dict[int, asyncio.Lock]] = {}
    _locks_guard: ClassVar[threading.Lock] = threading.Lock()

    @classmethod
    def _get_lock(cls) -> asyncio.Lock:
        """Return an ``asyncio.Lock`` bound to the current running loop."""
        loop = asyncio.get_running_loop()
        key = id(loop)
        with cls._locks_guard:
            lock = cls._locks.get(key)
            if lock is None:
                lock = asyncio.Lock()
                cls._locks[key] = lock
            # Drop any stale entries whose loops are closed; otherwise the dict
            # accumulates one entry per Lambda invocation forever.
            for stale_key in [
                k
                for k, candidate in cls._locks.items()
                if k != key
                and getattr(candidate, "_loop", None) is not None
                and candidate._loop.is_closed()  # type: ignore[attr-defined]
            ]:
                cls._locks.pop(stale_key, None)
        return lock

    # ============================================================================
    # Singleton Access
    # ============================================================================

    @classmethod
    async def get(cls: Type["App"]) -> Optional["App"]:
        """Get the App node from the graph, with caching.

        This method provides convenient singleton-like access to the App node.
        It traverses from Root -> App and caches the result for subsequent calls.

        The cache hit additionally re-resolves the cached instance against the
        **current** ``GraphContext`` database. Tests, embedded hosts, and any
        process that swaps DB contexts can otherwise hand out an App node from
        the previous database — silent multi-tenant corruption. AUDIT-core C-1.

        ``_cached_app`` reads/writes go through ``_locks_guard`` (a
        :class:`threading.Lock`) so concurrent workers in the same process
        cannot race on first fetch. AUDIT-core C-2.

        Returns:
            App node if found, None otherwise
        """
        # Fast-path: cache hit, but verify the cached node is reachable in
        # the *current* DB context (handles context swaps between calls).
        cached = cls._read_cached_app()
        if cached is not None:
            verified = await cls._verify_cached_against_current_context(cached)
            if verified is not None:
                return verified
            # Cached instance no longer belongs to the active DB; drop it.
            cls._set_cached_app(None)

        # Use lock to prevent concurrent fetches.
        async with cls._get_lock():
            # Double-check after acquiring lock.
            cached = cls._read_cached_app()
            if cached is not None:
                verified = await cls._verify_cached_against_current_context(cached)
                if verified is not None:
                    return verified
                cls._set_cached_app(None)

            # Get Root node.
            root = await Root.get()
            if not root:
                return None

            # Get App node connected to Root.
            app_nodes = await root.nodes()
            app_candidates = [n for n in app_nodes if isinstance(n, App)]
            if not app_candidates:
                # App node not found.
                return None
            # AUDIT-core M-1: warn (and tie-break deterministically) when
            # multiple App nodes hang off Root. Boot-time
            # ``_deduplicate_app_nodes`` should make this a no-op, but
            # later manual edits / partial restores can leave duplicates.
            # Tie-break: smallest id wins (lexicographic, deterministic).
            if len(app_candidates) > 1:
                ids = sorted(
                    [getattr(a, "id", "") for a in app_candidates]
                )
                logger.warning(
                    "App.get(): found %d App nodes on Root (%s); returning %r. "
                    "Run graph repair / dedupe to remove the others.",
                    len(app_candidates),
                    ids,
                    ids[0],
                )
                app_candidates.sort(key=lambda a: getattr(a, "id", "") or "")
            chosen = app_candidates[0]
            cls._set_cached_app(chosen)
            return chosen

    @classmethod
    def _read_cached_app(cls) -> Optional["App"]:
        """Thread-safe read of ``_cached_app``."""
        with cls._locks_guard:
            return cls._cached_app

    @classmethod
    def _set_cached_app(cls, value: Optional["App"]) -> None:
        """Thread-safe write of ``_cached_app``.

        Public so :mod:`jvagent.core.app_loader` can populate the cache
        through the same guard rather than touching the class attribute
        directly. AUDIT-core C-2.
        """
        with cls._locks_guard:
            cls._cached_app = value

    @classmethod
    async def _verify_cached_against_current_context(
        cls, cached: "App"
    ) -> Optional["App"]:
        """Return the cached App iff it is reachable in the current context.

        Returns ``None`` if the cached node belongs to a different DB context
        (e.g. test fixture swap, embedded-host context change).
        """
        cached_id = getattr(cached, "id", None)
        if not cached_id:
            return None
        try:
            ctx = get_default_context()
            live = await ctx.get(Node, cached_id)
        except Exception:
            return None
        if live is None:
            return None
        return cached

    @classmethod
    def clear_cache(cls) -> None:
        """Clear the cached App instance.

        This is useful when the App node might have been deleted or recreated,
        or when you want to force a fresh lookup on the next get() call.
        """
        cls._set_cached_app(None)

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

        # Align with get_file_storage_config / Server: env overrides, then node / defaults
        provider = env("JVSPATIAL_FILE_STORAGE_PROVIDER", default="") or (
            self.file_storage_provider or "local"
        )
        root_dir = resolve_file_storage_root(self.file_storage_root_dir or None)

        # Create storage based on resolved configuration
        if provider == "local":
            self._file_interface = create_storage(provider="local", root_dir=root_dir)
        elif provider == "s3":
            self._file_interface = create_storage(
                provider="s3",
                bucket_name=env("JVSPATIAL_S3_BUCKET_NAME", default=""),
                region_name=env("JVSPATIAL_S3_REGION", default="us-east-1"),
                access_key_id=env("JVSPATIAL_S3_ACCESS_KEY", default="") or None,
                secret_access_key=env("JVSPATIAL_S3_SECRET_KEY", default="") or None,
                endpoint_url=env("JVSPATIAL_S3_ENDPOINT_URL", default="") or None,
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

            # Relative URL: matches FileStorageService GET {FILES_ROOT}/{file_path}
            return f"{APIRoutes.FILES_ROOT}/{path}"
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
                pp = str(APIRoutes.PROXY_PREFIX).rstrip("/")
                return f"{pp}/{proxy.code}"
        except Exception:
            # Use regular file URL
            return await self.get_file_url(path)

        return None


async def app_now_aware_utc(app: Optional[App]) -> datetime:
    """Return a timezone-aware UTC datetime for scheduling comparisons.

    Normalizes :meth:`App.now` (typed as ``Union[datetime, str]``) and naïve
    datetimes from the app clock.
    """
    if app is None:
        now = datetime.now(timezone.utc)
    else:
        raw = await app.now()
        if isinstance(raw, str):
            now = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        else:
            now = raw
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now


async def set_app_update_mode(app: App, value: str) -> None:
    """Persist operational ``update_mode`` (protected field; bypasses normal setattr)."""
    object.__setattr__(app, "update_mode", value)
    await app.save()
