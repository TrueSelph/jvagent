"""Profiling utilities for measuring execution pipeline performance.

This module provides instrumentation for measuring latency at various points
in the jvagent execution pipeline. Enable profiling via:

    - app.yaml: config.performance.enable_profiling: true
    - Environment variable: JVAGENT_ENABLE_PROFILING=true

When enabled, profiles are logged at DEBUG level with latency breakdowns.
"""

import asyncio
import contextvars
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)

# Context variable for the current request profile (async-safe)
_current_profile: contextvars.ContextVar[Optional["RequestProfile"]] = (
    contextvars.ContextVar("current_profile", default=None)
)


def _get_profiling_config() -> bool:
    """Get profiling configuration with app.yaml and environment variable support.

    Configuration priority:
    1. Environment variable JVAGENT_ENABLE_PROFILING (highest)
    2. app.yaml config.performance.enable_profiling
    3. Default: false
    """
    # Environment variable takes priority
    env_value = os.getenv("JVAGENT_ENABLE_PROFILING")
    if env_value is not None:
        return env_value.lower() == "true"

    # Try app.yaml
    try:
        from jvagent.core.app_context import get_app_root
        from jvagent.core.app_loader import AppLoader

        loader = AppLoader(get_app_root())
        descriptor = loader.load_app_descriptor()

        if descriptor and descriptor.config:
            perf_config = descriptor.config.get("performance", {})
            if "enable_profiling" in perf_config:
                return bool(perf_config["enable_profiling"])
    except Exception:
        pass

    return False


# Configuration - initially False, populated when reload_profiling_config() is called
ENABLE_PROFILING = False


def reload_profiling_config() -> None:
    """Reload profiling configuration from app.yaml.

    This should be called after set_app_root() to ensure the config
    is loaded from the correct app.yaml location.
    """
    global ENABLE_PROFILING

    ENABLE_PROFILING = _get_profiling_config()

    logger.debug(f"Profiling config reloaded: enabled={ENABLE_PROFILING}")


# Profile TTL in seconds - profiles older than this will be cleaned up
# Default: 5 minutes (covers long-running requests with margin)
PROFILE_TTL = int(os.getenv("JVAGENT_PROFILE_TTL", "300"))

# Maximum number of profiles to keep in context
# Prevents unbounded growth in high-traffic scenarios
MAX_PROFILES = int(os.getenv("JVAGENT_MAX_PROFILES", "1000"))


@dataclass
class RequestProfile:
    """Profile for tracking latency across a single request."""

    request_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    timings: Dict[str, float] = field(default_factory=dict)
    start_time: float = field(default_factory=time.time)
    _nested_labels: Dict[str, float] = field(default_factory=dict, repr=False)

    def record(self, label: str, duration: float) -> None:
        """Record a timing measurement.

        Args:
            label: Descriptive label for the measurement
            duration: Duration in seconds
        """
        if label in self.timings:
            # Accumulate if label already exists (e.g., multiple action executions)
            self.timings[label] += duration
        else:
            self.timings[label] = duration

    @asynccontextmanager
    async def measure(self, label: str):
        """Context manager for measuring async operation duration.

        Args:
            label: Descriptive label for the measurement

        Example:
            async with profile.measure("agent_lookup"):
                agent = await Agent.get(agent_id)
        """
        start = time.time()
        try:
            yield
        finally:
            self.record(label, time.time() - start)

    def start_measurement(self, label: str) -> None:
        """Start a manual measurement (for non-context-manager use).

        Args:
            label: Descriptive label for the measurement
        """
        self._nested_labels[label] = time.time()

    def end_measurement(self, label: str) -> None:
        """End a manual measurement started with start_measurement.

        Args:
            label: Label matching the start_measurement call
        """
        if label in self._nested_labels:
            self.record(label, time.time() - self._nested_labels[label])
            del self._nested_labels[label]

    def summary(self) -> Dict[str, Any]:
        """Get a summary of all timings.

        Returns:
            Dictionary with request_id, total_ms, and breakdown_ms
        """
        total = time.time() - self.start_time
        return {
            "request_id": self.request_id,
            "total_ms": round(total * 1000, 2),
            "breakdown_ms": {k: round(v * 1000, 2) for k, v in self.timings.items()},
        }

    def log_summary(self, level: int = logging.DEBUG) -> None:
        """Log the profile summary.

        Args:
            level: Logging level to use
        """
        summary = self.summary()
        breakdown = summary["breakdown_ms"]
        breakdown_str = ", ".join(f"{k}={v}ms" for k, v in breakdown.items())
        logger.log(
            level,
            f"[Profile:{summary['request_id']}] Total: {summary['total_ms']}ms | {breakdown_str}",
        )


# Thread-local storage for request profiles (using asyncio context)
_profile_context: Dict[str, RequestProfile] = {}
_profile_lock = asyncio.Lock()


async def get_or_create_profile(request_id: Optional[str] = None) -> RequestProfile:
    """Get or create a profile for the current request.

    Args:
        request_id: Optional request ID. If not provided, generates a new one.

    Returns:
        RequestProfile instance for the request
    """
    if not ENABLE_PROFILING:
        # Return a no-op profile that doesn't store anything
        return RequestProfile()

    rid = request_id or str(uuid.uuid4())[:8]
    async with _profile_lock:
        if rid not in _profile_context:
            # Enforce max profiles limit before adding new one
            if len(_profile_context) >= MAX_PROFILES:
                # Remove oldest profiles (by start_time)
                sorted_profiles = sorted(
                    _profile_context.items(), key=lambda x: x[1].start_time
                )
                # Remove 10% of oldest profiles to make room
                to_remove = max(1, len(sorted_profiles) // 10)
                for i in range(to_remove):
                    del _profile_context[sorted_profiles[i][0]]
                logger.debug(f"Profile cleanup: removed {to_remove} oldest profiles")

            _profile_context[rid] = RequestProfile(request_id=rid)
        return _profile_context[rid]


async def finalize_profile(
    request_id: str, log: bool = True
) -> Optional[Dict[str, Any]]:
    """Finalize and optionally log a request profile.

    Args:
        request_id: Request ID to finalize
        log: Whether to log the summary

    Returns:
        Profile summary dictionary, or None if profiling disabled
    """
    if not ENABLE_PROFILING:
        return None

    async with _profile_lock:
        profile = _profile_context.pop(request_id, None)

    if profile:
        if log:
            profile.log_summary()
        return profile.summary()
    return None


def profile_enabled() -> bool:
    """Check if profiling is enabled.

    Returns:
        True if profiling is enabled
    """
    return ENABLE_PROFILING


@asynccontextmanager
async def profiled_request(request_id: Optional[str] = None):
    """Context manager for profiling an entire request.

    Automatically creates a profile at the start and finalizes it at the end.

    Args:
        request_id: Optional request ID

    Yields:
        RequestProfile instance

    Example:
        async with profiled_request("req-123") as profile:
            async with profile.measure("agent_lookup"):
                agent = await Agent.get(agent_id)
            # ... more operations
        # Profile is automatically logged and cleaned up
    """
    profile = await get_or_create_profile(request_id)
    try:
        yield profile
    finally:
        if ENABLE_PROFILING:
            await finalize_profile(profile.request_id, log=True)


async def cleanup_stale_profiles() -> int:
    """Clean up stale profiles that have exceeded the TTL.

    This function removes profiles that have been in the context for longer
    than PROFILE_TTL seconds. This handles cases where finalize_profile()
    was never called (e.g., due to exceptions or missing cleanup).

    Returns:
        Number of profiles removed
    """
    if not ENABLE_PROFILING:
        return 0

    now = time.time()
    removed = 0

    async with _profile_lock:
        stale_ids = [
            rid
            for rid, profile in _profile_context.items()
            if (now - profile.start_time) >= PROFILE_TTL
        ]
        for rid in stale_ids:
            del _profile_context[rid]
            removed += 1

    if removed > 0:
        logger.debug(
            f"Profile cleanup: removed {removed} stale profiles (TTL: {PROFILE_TTL}s)"
        )

    return removed


async def get_profile_stats() -> Dict[str, Any]:
    """Get statistics about the profile context.

    Returns:
        Dictionary with profile statistics
    """
    now = time.time()

    async with _profile_lock:
        size = len(_profile_context)
        stale_count = sum(
            1
            for profile in _profile_context.values()
            if (now - profile.start_time) >= PROFILE_TTL
        )

    return {
        "enabled": ENABLE_PROFILING,
        "size": size,
        "stale": stale_count,
        "max_profiles": MAX_PROFILES,
        "ttl_seconds": PROFILE_TTL,
    }


# ============================================================================
# Profile Context Propagation (for LM call tracking)
# ============================================================================


def set_current_profile(profile: Optional["RequestProfile"]) -> None:
    """Set the current profile in async context.

    This allows nested code (like LM calls) to record timing to the
    current request's profile without explicit parameter passing.

    Args:
        profile: The RequestProfile to set, or None to clear
    """
    _current_profile.set(profile)


def get_current_profile() -> Optional["RequestProfile"]:
    """Get the current profile from async context.

    Returns:
        The current RequestProfile if set, None otherwise
    """
    return _current_profile.get()


def record_lm_call(label: str, duration: float) -> None:
    """Record an LM API call duration to the current profile.

    This is a convenience function for recording language model call
    timings. It safely handles the case where no profile is set or
    profiling is disabled.

    Args:
        label: Label for the LM call (e.g., "lm:PersonaAction")
        duration: Duration of the call in seconds
    """
    if not ENABLE_PROFILING:
        return

    profile = _current_profile.get()
    if profile is not None:
        profile.record(label, duration)
