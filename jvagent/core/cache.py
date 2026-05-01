"""Caching layer for Agent, Memory, and Action nodes to reduce database I/O."""

import asyncio
import hashlib
import json
import logging
import random
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


async def _get_now() -> datetime:
    """Current datetime in app timezone, or UTC if App unavailable."""
    try:
        from jvagent.core.app import App

        app = await App.get()
        if app:
            return await app.now()
    except Exception:
        pass
    return datetime.now(timezone.utc)


def _load_perf_config() -> Dict[str, Any]:
    """Load app config from app.yaml for performance section."""
    try:
        from jvagent.core.app_context import get_app_root
        from jvagent.core.config import load_app_config

        return load_app_config(get_app_root())
    except Exception as e:
        logger.debug("Could not load performance config from app.yaml: %s", e)
        return {}


def _perf_config_value(
    config: Dict[str, Any],
    key: str,
    env_var: str,
    default: Any,
    config_type: type = str,
) -> Any:
    """Get performance config value via centralized config module."""
    from jvagent.core.config import get_performance_config_value

    return get_performance_config_value(config, key, env_var, default, config_type)


class CacheManager:
    """Encapsulated cache state and TTL-based expiration for agent, action, and router caches.

    Instantiated once as a module-level singleton. Config is reloaded from
    app.yaml via :meth:`reload_config`.
    """

    def __init__(self) -> None:
        self._agent_cache: Dict[str, Tuple[Any, datetime]] = {}
        self._agent_lock = asyncio.Lock()

        self._action_cache: Dict[str, Tuple[List[Any], datetime]] = {}
        self._action_lock = asyncio.Lock()

        self._router_cache: Dict[str, Tuple[Dict[str, Any], datetime]] = {}
        self._router_lock = asyncio.Lock()

        # Action type index: {agent_id: {class_name: action_id}}
        self._action_type_index: Dict[str, Dict[str, str]] = {}
        self._action_type_lock = asyncio.Lock()

        self._config: Dict[str, Any] = {}
        self._load_defaults()

    def _load_defaults(self) -> None:
        self.agent_cache_enabled: bool = True
        self.agent_cache_ttl: int = 300
        self.action_cache_enabled: bool = True
        self.action_cache_ttl: int = 60
        self.router_cache_enabled: bool = False
        self.router_cache_ttl: int = 45
        self.cleanup_probability: float = 0.1

    def reload_config(self) -> None:
        """Reload performance configuration from app.yaml."""
        self._config = _load_perf_config()
        self.agent_cache_enabled = _perf_config_value(
            self._config, "enable_agent_cache", "JVAGENT_ENABLE_AGENT_CACHE", True, bool
        )
        self.agent_cache_ttl = _perf_config_value(
            self._config, "agent_cache_ttl", "JVAGENT_AGENT_CACHE_TTL", 300, int
        )
        self.action_cache_enabled = _perf_config_value(
            self._config,
            "enable_action_cache",
            "JVAGENT_ENABLE_ACTION_CACHE",
            True,
            bool,
        )
        self.action_cache_ttl = _perf_config_value(
            self._config, "action_cache_ttl", "JVAGENT_ACTION_CACHE_TTL", 60, int
        )
        self.cleanup_probability = _perf_config_value(
            self._config,
            "cache_cleanup_probability",
            "JVAGENT_CACHE_CLEANUP_PROBABILITY",
            0.1,
            float,
        )
        self.router_cache_enabled = _perf_config_value(
            self._config,
            "enable_interact_router_cache",
            "JVAGENT_ENABLE_INTERACT_ROUTER_CACHE",
            False,
            bool,
        )
        self.router_cache_ttl = _perf_config_value(
            self._config,
            "interact_router_cache_ttl",
            "JVAGENT_INTERACT_ROUTER_CACHE_TTL",
            45,
            int,
        )
        logger.debug(
            "Performance config reloaded: agent=%s action=%s router=%s",
            self.agent_cache_enabled,
            self.action_cache_enabled,
            self.router_cache_enabled,
        )

    # -- Agent cache ----------------------------------------------------------

    async def _fetch_agent_uncached(self, agent_id: str) -> Optional[Any]:
        """Direct DB fetch bypassing Agent.get() caching (avoids recursion)."""
        from jvspatial.core import Node

        return await Node.get(agent_id)

    async def get_agent(self, agent_id: str) -> Optional[Any]:
        if not self.agent_cache_enabled:
            return await self._fetch_agent_uncached(agent_id)

        async with self._agent_lock:
            entry = self._agent_cache.get(agent_id)
            if entry:
                agent, cached_at = entry
                now = await _get_now()
                if (now - cached_at).total_seconds() < self.agent_cache_ttl:
                    return agent
                del self._agent_cache[agent_id]

        agent = await self._fetch_agent_uncached(agent_id)
        if agent:
            async with self._agent_lock:
                self._agent_cache[agent_id] = (agent, await _get_now())
        return agent

    async def invalidate_agent(self, agent_id: Optional[str] = None) -> None:
        async with self._agent_lock:
            if agent_id:
                self._agent_cache.pop(agent_id, None)
            else:
                self._agent_cache.clear()

    async def get_memory(self, agent_id: str) -> Optional[Any]:
        agent = await self.get_agent(agent_id)
        if not agent:
            return None
        return await agent.get_memory()

    # -- Action cache ---------------------------------------------------------

    def _action_key(self, agent_id: str, enabled_only: bool) -> str:
        return f"{agent_id}:{'enabled' if enabled_only else 'all'}"

    async def get_actions(
        self, agent_id: str, enabled_only: bool = True
    ) -> Optional[List[Any]]:
        if not self.action_cache_enabled:
            return None
        key = self._action_key(agent_id, enabled_only)
        async with self._action_lock:
            entry = self._action_cache.get(key)
            if entry:
                actions, cached_at = entry
                now = await _get_now()
                if (now - cached_at).total_seconds() < self.action_cache_ttl:
                    return actions
                del self._action_cache[key]
        return None

    async def set_actions(
        self, agent_id: str, actions: List[Any], enabled_only: bool = True
    ) -> None:
        if not self.action_cache_enabled:
            return
        key = self._action_key(agent_id, enabled_only)
        async with self._action_lock:
            self._action_cache[key] = (actions, await _get_now())

    async def invalidate_actions(self, agent_id: Optional[str] = None) -> None:
        async with self._action_lock:
            if agent_id:
                keys = [k for k in self._action_cache if k.startswith(f"{agent_id}:")]
                for k in keys:
                    del self._action_cache[k]
            else:
                self._action_cache.clear()

    # -- Action type index ----------------------------------------------------

    async def get_action_by_type(self, agent_id: str, class_name: str) -> Optional[str]:
        """Return action_id for the given *class_name* under *agent_id*, or None."""
        async with self._action_type_lock:
            return self._action_type_index.get(agent_id, {}).get(class_name)

    async def set_action_type_index(
        self, agent_id: str, class_name: str, action_id: str
    ) -> None:
        async with self._action_type_lock:
            self._action_type_index.setdefault(agent_id, {})[class_name] = action_id

    async def invalidate_action_type_index(
        self, agent_id: Optional[str] = None
    ) -> None:
        async with self._action_type_lock:
            if agent_id:
                self._action_type_index.pop(agent_id, None)
            else:
                self._action_type_index.clear()

    # -- Router cache ---------------------------------------------------------

    @staticmethod
    def router_cache_key(
        conversation_id: str,
        utterance: str,
        last_interaction_ids: Tuple[str, ...],
        buffer_fingerprint: str,
        active_task_fingerprint: str,
        proactive_tasks_fingerprint: str = "",
    ) -> str:
        payload = json.dumps(
            {
                "conversation_id": conversation_id,
                "utterance": utterance,
                "last_ids": last_interaction_ids,
                "buffer": buffer_fingerprint,
                "active_tasks": active_task_fingerprint,
                "proactive_tasks": proactive_tasks_fingerprint,
            },
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode()).hexdigest()

    async def get_router(
        self, cache_key: str, caller_enabled: bool = True
    ) -> Optional[Dict[str, Any]]:
        if not caller_enabled or not self.router_cache_enabled:
            return None
        async with self._router_lock:
            entry = self._router_cache.get(cache_key)
            if entry:
                data, cached_at = entry
                now = await _get_now()
                if (now - cached_at).total_seconds() < self.router_cache_ttl:
                    return data
                del self._router_cache[cache_key]
        return None

    async def set_router(
        self, cache_key: str, result: Dict[str, Any], caller_enabled: bool = True
    ) -> None:
        if not caller_enabled or not self.router_cache_enabled:
            return
        payload = {k: v for k, v in result.items() if k != "reasoning"}
        async with self._router_lock:
            self._router_cache[cache_key] = (payload, await _get_now())

    # -- Cleanup --------------------------------------------------------------

    async def cleanup_expired(self) -> bool:
        now = await _get_now()
        cleaned = 0

        async with self._agent_lock:
            expired = [
                aid
                for aid, (_, ts) in self._agent_cache.items()
                if (now - ts).total_seconds() >= self.agent_cache_ttl
            ]
            for aid in expired:
                del self._agent_cache[aid]
            cleaned += len(expired)

        async with self._action_lock:
            expired = [
                k
                for k, (_, ts) in self._action_cache.items()
                if (now - ts).total_seconds() >= self.action_cache_ttl
            ]
            for k in expired:
                del self._action_cache[k]
            cleaned += len(expired)

        async with self._router_lock:
            expired = [
                k
                for k, (_, ts) in self._router_cache.items()
                if (now - ts).total_seconds() >= self.router_cache_ttl
            ]
            for k in expired:
                del self._router_cache[k]
            cleaned += len(expired)

        try:
            from jvagent.core.profiling import cleanup_stale_profiles

            cleaned += await cleanup_stale_profiles()
        except Exception:
            pass

        if cleaned:
            logger.debug("Cache cleanup: removed %d expired entries", cleaned)
        return cleaned > 0

    async def maybe_cleanup_on_request(self) -> bool:
        if self.cleanup_probability <= 0 or random.random() > self.cleanup_probability:
            return False
        try:
            return await self.cleanup_expired()
        except Exception as e:
            logger.debug("Request-scoped cache cleanup error (non-fatal): %s", e)
            return False

    async def get_stats(self) -> Dict[str, Any]:
        now = await _get_now()

        async with self._agent_lock:
            agent_expired = sum(
                1
                for _, (_, ts) in self._agent_cache.items()
                if (now - ts).total_seconds() >= self.agent_cache_ttl
            )
        async with self._action_lock:
            action_expired = sum(
                1
                for _, (_, ts) in self._action_cache.items()
                if (now - ts).total_seconds() >= self.action_cache_ttl
            )
        async with self._router_lock:
            router_size = len(self._router_cache)

        return {
            "agent_cache": {
                "enabled": self.agent_cache_enabled,
                "size": len(self._agent_cache),
                "expired": agent_expired,
                "ttl_seconds": self.agent_cache_ttl,
            },
            "action_cache": {
                "enabled": self.action_cache_enabled,
                "size": len(self._action_cache),
                "expired": action_expired,
                "ttl_seconds": self.action_cache_ttl,
            },
            "interact_router_cache": {
                "enabled": self.router_cache_enabled,
                "size": router_size,
                "ttl_seconds": self.router_cache_ttl,
            },
        }


# Module-level singleton
cache_manager = CacheManager()


# ============================================================================
# Public API — delegates to the singleton CacheManager
# ============================================================================


def reload_performance_config() -> None:
    """Reload performance configuration from app.yaml."""
    cache_manager.reload_config()


async def get_cached_agent(agent_id: str) -> Optional[Any]:
    return await cache_manager.get_agent(agent_id)


async def invalidate_agent_cache(agent_id: Optional[str] = None) -> None:
    await cache_manager.invalidate_agent(agent_id)


async def get_cached_memory(agent_id: str) -> Optional[Any]:
    return await cache_manager.get_memory(agent_id)


async def get_cached_actions(
    agent_id: str, enabled_only: bool = True
) -> Optional[List[Any]]:
    return await cache_manager.get_actions(agent_id, enabled_only)


async def cache_actions(
    agent_id: str, actions: List[Any], enabled_only: bool = True
) -> None:
    await cache_manager.set_actions(agent_id, actions, enabled_only)


async def invalidate_action_cache(agent_id: Optional[str] = None) -> None:
    await cache_manager.invalidate_actions(agent_id)


async def get_cached_action_id_by_type(agent_id: str, class_name: str) -> Optional[str]:
    return await cache_manager.get_action_by_type(agent_id, class_name)


async def cache_action_type_index(
    agent_id: str, class_name: str, action_id: str
) -> None:
    await cache_manager.set_action_type_index(agent_id, class_name, action_id)


async def invalidate_action_type_index(agent_id: Optional[str] = None) -> None:
    await cache_manager.invalidate_action_type_index(agent_id)


def interact_router_cache_key(
    conversation_id: str,
    utterance: str,
    last_interaction_ids: Tuple[str, ...],
    buffer_fingerprint: str,
    active_task_fingerprint: str,
    proactive_tasks_fingerprint: str = "",
) -> str:
    return cache_manager.router_cache_key(
        conversation_id,
        utterance,
        last_interaction_ids,
        buffer_fingerprint,
        active_task_fingerprint,
        proactive_tasks_fingerprint,
    )


async def get_interact_router_cache(
    cache_key: str, caller_enabled: bool = True
) -> Optional[Dict[str, Any]]:
    return await cache_manager.get_router(cache_key, caller_enabled)


async def set_interact_router_cache(
    cache_key: str, result: Dict[str, Any], caller_enabled: bool = True
) -> None:
    await cache_manager.set_router(cache_key, result, caller_enabled)


async def get_cache_stats() -> Dict[str, Any]:
    return await cache_manager.get_stats()


async def cleanup_expired_entries() -> bool:
    return await cache_manager.cleanup_expired()


async def maybe_cleanup_on_request() -> bool:
    return await cache_manager.maybe_cleanup_on_request()
