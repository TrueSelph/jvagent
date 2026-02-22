"""Caching layer for Agent, Memory, and Action nodes to reduce database I/O."""

import asyncio
import logging
import os
import random
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ============================================================================
# Performance Configuration Loader
# ============================================================================
def _load_performance_config() -> Dict[str, Any]:
    """Load performance configuration from app.yaml with environment variable fallback.

    Configuration priority:
    1. Environment variables (highest priority)
    2. app.yaml config.performance section
    3. Default values (lowest priority)

    Returns:
        Dictionary with performance configuration values
    """
    config: Dict[str, Any] = {}

    # Try to load from app.yaml
    try:
        from jvagent.core.app_context import get_app_root
        from jvagent.core.app_loader import AppLoader

        loader = AppLoader(get_app_root())
        descriptor = loader.load_app_descriptor()

        if descriptor and descriptor.config:
            perf_config = descriptor.config.get("performance", {})
            if perf_config:
                config = perf_config
                logger.debug("Loaded performance config from app.yaml")
    except Exception as e:
        logger.debug(f"Could not load performance config from app.yaml: {e}")

    return config


# Load config - initially empty, populated when reload_performance_config() is called
_perf_config: Dict[str, Any] = {}


def reload_performance_config() -> None:
    """Reload performance configuration from app.yaml.

    This should be called after set_app_root() to ensure the config
    is loaded from the correct app.yaml location.
    """
    global _perf_config, ENABLE_AGENT_CACHING, AGENT_CACHE_TTL
    global ENABLE_ACTION_CACHE, ACTION_CACHE_TTL, CACHE_CLEANUP_PROBABILITY

    _perf_config = _load_performance_config()

    # Reload all config values
    ENABLE_AGENT_CACHING = _get_config_value(
        "enable_agent_caching", "JVAGENT_ENABLE_AGENT_CACHING", True, bool
    )
    AGENT_CACHE_TTL = _get_config_value(
        "agent_cache_ttl", "JVAGENT_AGENT_CACHE_TTL", 300, int
    )
    ENABLE_ACTION_CACHE = _get_config_value(
        "enable_action_cache", "JVAGENT_ENABLE_ACTION_CACHE", True, bool
    )
    ACTION_CACHE_TTL = _get_config_value(
        "action_cache_ttl", "JVAGENT_ACTION_CACHE_TTL", 60, int
    )
    CACHE_CLEANUP_PROBABILITY = _get_config_value(
        "cache_cleanup_probability", "JVAGENT_CACHE_CLEANUP_PROBABILITY", 0.1, float
    )

    logger.debug(
        f"Performance config reloaded: agent_caching={ENABLE_AGENT_CACHING}, "
        f"action_caching={ENABLE_ACTION_CACHE}"
    )


def _get_config_value(
    key: str, env_var: str, default: Any, config_type: type = str
) -> Any:
    """Get configuration value with environment variable priority.

    Args:
        key: Key in the performance config section
        env_var: Environment variable name
        default: Default value if neither config nor env var is set
        config_type: Type to convert the value to (str, int, float, bool)

    Returns:
        Configuration value
    """
    # Environment variable takes priority
    env_value = os.getenv(env_var)
    if env_value is not None:
        if config_type == bool:
            return env_value.lower() == "true"
        elif config_type == int:
            return int(env_value)
        elif config_type == float:
            return float(env_value)
        return env_value

    # Check app.yaml config
    if key in _perf_config:
        return _perf_config[key]

    # Return default
    return default


# ============================================================================
# Agent Cache Configuration
# ============================================================================
ENABLE_AGENT_CACHING = _get_config_value(
    "enable_agent_caching", "JVAGENT_ENABLE_AGENT_CACHING", True, bool
)
AGENT_CACHE_TTL = _get_config_value(
    "agent_cache_ttl", "JVAGENT_AGENT_CACHE_TTL", 300, int
)

# In-memory cache: {agent_id: (agent_node, cached_at)}
_agent_cache: Dict[str, Tuple[Any, datetime]] = {}
_cache_lock = asyncio.Lock()

# ============================================================================
# Action Cache Configuration
# ============================================================================
ENABLE_ACTION_CACHE = _get_config_value(
    "enable_action_cache", "JVAGENT_ENABLE_ACTION_CACHE", True, bool
)
ACTION_CACHE_TTL = _get_config_value(
    "action_cache_ttl", "JVAGENT_ACTION_CACHE_TTL", 60, int
)

# Action cache: {cache_key: (actions_list, cached_at)}
# cache_key format: "{agent_id}:enabled" or "{agent_id}:all"
_action_cache: Dict[str, Tuple[List[Any], datetime]] = {}
_action_cache_lock = asyncio.Lock()


async def get_cached_agent(agent_id: str) -> Optional[Any]:
    """Get an agent from cache or database.

    If caching is enabled, checks the in-memory cache first. If the agent
    is found in cache and hasn't expired, returns it. Otherwise, fetches
    from database and updates the cache.

    Args:
        agent_id: The ID of the agent to retrieve

    Returns:
        Agent node if found, None otherwise
    """
    if not ENABLE_AGENT_CACHING:
        # Caching disabled, fetch directly from database
        from jvagent.core.agent import Agent

        return await Agent.get(agent_id)

    async with _cache_lock:
        # Check cache
        if agent_id in _agent_cache:
            agent, cached_at = _agent_cache[agent_id]
            age = (datetime.now(timezone.utc) - cached_at).total_seconds()

            if age < AGENT_CACHE_TTL:
                # Cache hit and still valid
                logger.debug(f"Agent cache hit for {agent_id} (age: {age:.1f}s)")
                return agent
            else:
                # Cache expired, remove it
                logger.debug(f"Agent cache expired for {agent_id} (age: {age:.1f}s)")
                del _agent_cache[agent_id]

    # Cache miss or expired, fetch from database
    from jvagent.core.agent import Agent

    agent = await Agent.get(agent_id)

    if agent:
        # Update cache
        async with _cache_lock:
            _agent_cache[agent_id] = (agent, datetime.now(timezone.utc))
            logger.debug(f"Agent cached for {agent_id}")

    return agent


async def invalidate_agent_cache(agent_id: Optional[str] = None) -> None:
    """Invalidate agent cache entry(s).

    Args:
        agent_id: Specific agent ID to invalidate, or None to clear all cache
    """
    async with _cache_lock:
        if agent_id:
            if agent_id in _agent_cache:
                del _agent_cache[agent_id]
                logger.debug(f"Agent cache invalidated for {agent_id}")
        else:
            _agent_cache.clear()
            logger.debug("Agent cache cleared")


async def get_cached_memory(agent_id: str) -> Optional[Any]:
    """Get memory node for an agent from cache or database.

    This is a convenience method that gets the agent (from cache if available)
    and then retrieves its memory node.

    Args:
        agent_id: The ID of the agent

    Returns:
        Memory node if found, None otherwise
    """
    agent = await get_cached_agent(agent_id)
    if not agent:
        return None
    return await agent.get_memory()


async def get_cache_stats() -> Dict[str, Any]:
    """Get cache statistics for both agent and action caches.

    Returns:
        Dictionary with cache statistics including size, TTL, and enabled status
    """
    now = datetime.now(timezone.utc)

    async with _cache_lock:
        agent_cache_size = len(_agent_cache)
        agent_expired_count = 0

        for agent_id, (agent, cached_at) in _agent_cache.items():
            age = (now - cached_at).total_seconds()
            if age >= AGENT_CACHE_TTL:
                agent_expired_count += 1

    async with _action_cache_lock:
        action_cache_size = len(_action_cache)
        action_expired_count = 0

        for cache_key, (actions, cached_at) in _action_cache.items():
            age = (now - cached_at).total_seconds()
            if age >= ACTION_CACHE_TTL:
                action_expired_count += 1

    return {
        "agent_cache": {
            "enabled": ENABLE_AGENT_CACHING,
            "size": agent_cache_size,
            "expired": agent_expired_count,
            "ttl_seconds": AGENT_CACHE_TTL,
        },
        "action_cache": {
            "enabled": ENABLE_ACTION_CACHE,
            "size": action_cache_size,
            "expired": action_expired_count,
            "ttl_seconds": ACTION_CACHE_TTL,
        },
    }


# ============================================================================
# Action Cache Functions
# ============================================================================


async def get_cached_actions(
    agent_id: str, enabled_only: bool = True
) -> Optional[List[Any]]:
    """Get actions from cache if valid.

    Args:
        agent_id: The agent ID to get actions for
        enabled_only: If True, return only enabled actions

    Returns:
        List of action instances if cached and valid, None otherwise
    """
    if not ENABLE_ACTION_CACHE:
        return None

    cache_key = f"{agent_id}:{'enabled' if enabled_only else 'all'}"

    async with _action_cache_lock:
        if cache_key in _action_cache:
            actions, cached_at = _action_cache[cache_key]
            age = (datetime.now(timezone.utc) - cached_at).total_seconds()

            if age < ACTION_CACHE_TTL:
                logger.debug(f"Action cache hit for {cache_key} (age: {age:.1f}s)")
                return actions
            else:
                # Cache expired, remove it
                logger.debug(f"Action cache expired for {cache_key} (age: {age:.1f}s)")
                del _action_cache[cache_key]

    return None


async def cache_actions(
    agent_id: str, actions: List[Any], enabled_only: bool = True
) -> None:
    """Store actions in cache.

    Args:
        agent_id: The agent ID these actions belong to
        actions: List of action instances to cache
        enabled_only: Whether this is the enabled-only action list
    """
    if not ENABLE_ACTION_CACHE:
        return

    cache_key = f"{agent_id}:{'enabled' if enabled_only else 'all'}"

    async with _action_cache_lock:
        _action_cache[cache_key] = (actions, datetime.now(timezone.utc))
        logger.debug(f"Actions cached for {cache_key} ({len(actions)} actions)")


async def invalidate_action_cache(agent_id: Optional[str] = None) -> None:
    """Invalidate action cache entries.

    Args:
        agent_id: Specific agent ID to invalidate, or None to clear all
    """
    async with _action_cache_lock:
        if agent_id:
            # Remove all cache entries for this agent
            keys_to_remove = [k for k in _action_cache if k.startswith(f"{agent_id}:")]
            for key in keys_to_remove:
                del _action_cache[key]
            if keys_to_remove:
                logger.debug(f"Action cache invalidated for agent {agent_id}")
        else:
            _action_cache.clear()
            logger.debug("Action cache cleared")


# ============================================================================
# Request-Scoped Cache Cleanup
# ============================================================================

# Probability of cleanup running per request (0.0-1.0, default 0.1 = 10%)
# This approach is serverless-friendly (works in Lambda, Cloud Functions, etc.)
CACHE_CLEANUP_PROBABILITY = _get_config_value(
    "cache_cleanup_probability", "JVAGENT_CACHE_CLEANUP_PROBABILITY", 0.1, float
)


async def cleanup_expired_entries() -> bool:
    """Clean up expired cache entries (agent, action, profiles).

    This function removes expired entries from the agent cache, action cache,
    and profiling context. It can be called directly or via maybe_cleanup_on_request()
    for probabilistic cleanup at request boundaries.

    Returns:
        True if any entries were cleaned, False otherwise
    """
    now = datetime.now(timezone.utc)
    agent_cleaned = 0
    action_cleaned = 0
    profiles_cleaned = 0

    # Clean agent cache
    async with _cache_lock:
        expired_agents = [
            agent_id
            for agent_id, (_, cached_at) in _agent_cache.items()
            if (now - cached_at).total_seconds() >= AGENT_CACHE_TTL
        ]
        for agent_id in expired_agents:
            del _agent_cache[agent_id]
        agent_cleaned = len(expired_agents)

    # Clean action cache
    async with _action_cache_lock:
        expired_actions = [
            cache_key
            for cache_key, (_, cached_at) in _action_cache.items()
            if (now - cached_at).total_seconds() >= ACTION_CACHE_TTL
        ]
        for cache_key in expired_actions:
            del _action_cache[cache_key]
        action_cleaned = len(expired_actions)

    # Clean stale profiling contexts
    try:
        from jvagent.core.profiling import cleanup_stale_profiles

        profiles_cleaned = await cleanup_stale_profiles()
    except ImportError:
        pass  # Profiling module not available
    except Exception as e:
        logger.debug(f"Profile cleanup error (non-fatal): {e}")

    cleaned_any = agent_cleaned > 0 or action_cleaned > 0 or profiles_cleaned > 0

    if cleaned_any:
        logger.debug(
            f"Cache cleanup: removed {agent_cleaned} expired agents, "
            f"{action_cleaned} expired action entries, "
            f"{profiles_cleaned} stale profiles"
        )

    return cleaned_any


async def maybe_cleanup_on_request() -> bool:
    """Probabilistically run cache cleanup at request boundary.

    This function should be called at the end of each request (e.g., in a finally block).
    It will only actually perform cleanup based on CACHE_CLEANUP_PROBABILITY, making it
    efficient for high-traffic scenarios while still preventing memory accumulation.

    This approach is serverless-friendly and works in both traditional servers
    and environments like AWS Lambda where background tasks are not reliable.

    Returns:
        True if cleanup was performed, False if skipped
    """
    # Skip if cleanup is disabled
    if CACHE_CLEANUP_PROBABILITY <= 0:
        return False

    # Probabilistic check - only run cleanup on a fraction of requests
    if random.random() > CACHE_CLEANUP_PROBABILITY:
        return False

    # Perform cleanup
    try:
        return await cleanup_expired_entries()
    except Exception as e:
        logger.debug(f"Request-scoped cache cleanup error (non-fatal): {e}")
        return False
