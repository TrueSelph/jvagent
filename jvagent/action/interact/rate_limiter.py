"""Rate limiter and validation for interact endpoint.

This module provides rate limiting by IP and agent_id, as well as
utterance length validation for anonymous requests.
"""

import logging
import time
from collections import defaultdict
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


class InteractRateLimiter:
    """Rate limiter for interact endpoint requests.

    Implements sliding window rate limiting by IP address and agent_id combination.
    Also provides utterance length validation.
    """

    def __init__(
        self,
        rate_limit_per_minute: int = 60,
        max_utterance_length: Optional[int] = 2000,
    ):
        """Initialize the rate limiter.

        Args:
            rate_limit_per_minute: Maximum requests per minute per IP+agent_id
            max_utterance_length: Maximum characters for utterance (None to disable)
        """
        self.rate_limit_per_minute = rate_limit_per_minute
        self.max_utterance_length = max_utterance_length
        # Store request timestamps: key -> list of timestamps
        self._request_timestamps: dict[str, list[float]] = defaultdict(list)
        self._lock = None  # Will be set if threading is needed

    def check_rate_limit(self, ip: str, agent_id: str) -> bool:
        """Check if request is within rate limit.

        Args:
            ip: Client IP address
            agent_id: Agent identifier

        Returns:
            True if within rate limit, False if exceeded
        """
        if not ip:
            # If no IP, allow the request (shouldn't happen in practice)
            logger.warning("Rate limit check called without IP address")
            return True

        key = f"{ip}:{agent_id}"
        now = time.time()
        window_start = now - 60.0  # 60 second window

        # Clean old timestamps outside the window
        if key in self._request_timestamps:
            self._request_timestamps[key] = [
                ts for ts in self._request_timestamps[key] if ts > window_start
            ]

        # Check if limit exceeded
        current_count = len(self._request_timestamps[key])
        if current_count >= self.rate_limit_per_minute:
            logger.warning(
                f"Rate limit exceeded for {key}: {current_count}/{self.rate_limit_per_minute} requests"
            )
            return False

        return True

    def record_request(self, ip: str, agent_id: str) -> None:
        """Record a request for rate limiting.

        Args:
            ip: Client IP address
            agent_id: Agent identifier
        """
        if not ip:
            return

        key = f"{ip}:{agent_id}"
        now = time.time()
        self._request_timestamps[key].append(now)

        # Periodic cleanup of old entries (every 100 requests to avoid overhead)
        if len(self._request_timestamps) > 1000:
            self._cleanup_old_entries()

    def _cleanup_old_entries(self) -> None:
        """Clean up old entries from the rate limit cache."""
        now = time.time()
        window_start = now - 60.0
        keys_to_remove = []

        for key, timestamps in self._request_timestamps.items():
            # Remove old timestamps
            filtered = [ts for ts in timestamps if ts > window_start]
            if filtered:
                self._request_timestamps[key] = filtered
            else:
                keys_to_remove.append(key)

        # Remove empty entries
        for key in keys_to_remove:
            del self._request_timestamps[key]

    def validate_utterance_length(
        self, utterance: str, max_length: Optional[int] = None
    ) -> Tuple[bool, Optional[str]]:
        """Validate utterance length.

        Args:
            utterance: The utterance to validate
            max_length: Maximum allowed length (None uses configured default)

        Returns:
            Tuple of (is_valid, error_message)
            - (True, None) if valid
            - (False, error_message) if exceeded
        """
        if max_length is None:
            max_length = self.max_utterance_length

        # If validation is disabled, always return valid
        if max_length is None:
            return (True, None)

        current_length = len(utterance)
        if current_length > max_length:
            error_message = (
                f"utterance exceeds maximum length of {max_length} characters "
                f"(current: {current_length} characters)"
            )
            return (False, error_message)

        return (True, None)


# Global rate limiter instance (will be initialized with config)
_rate_limiter: Optional[InteractRateLimiter] = None


def get_rate_limiter() -> InteractRateLimiter:
    """Get the global rate limiter instance.

    Returns:
        InteractRateLimiter instance
    """
    global _rate_limiter
    if _rate_limiter is None:
        # Initialize with defaults (will be updated from config if available)
        _rate_limiter = InteractRateLimiter()
    return _rate_limiter


def initialize_rate_limiter(
    rate_limit_per_minute: int = 60, max_utterance_length: Optional[int] = 2000
) -> None:
    """Initialize the global rate limiter with configuration.

    Args:
        rate_limit_per_minute: Maximum requests per minute per IP+agent_id
        max_utterance_length: Maximum characters for utterance (None to disable)
    """
    global _rate_limiter
    _rate_limiter = InteractRateLimiter(
        rate_limit_per_minute=rate_limit_per_minute,
        max_utterance_length=max_utterance_length,
    )


def extract_client_ip(request) -> Optional[str]:
    """Extract client IP address from request headers.

    Handles proxy headers in order:
    1. X-Forwarded-For (first IP in comma-separated list)
    2. X-Real-IP
    3. CF-Connecting-IP (Cloudflare)
    4. request.client.host (fallback)

    Args:
        request: FastAPI Request object

    Returns:
        IP address string or None if unavailable
    """
    # Check X-Forwarded-For header (first IP in comma-separated list)
    x_forwarded_for = request.headers.get("x-forwarded-for")
    if x_forwarded_for:
        # X-Forwarded-For can contain multiple IPs, take the first one
        ip = x_forwarded_for.split(",")[0].strip()
        if ip:
            return ip

    # Check X-Real-IP header
    x_real_ip = request.headers.get("x-real-ip")
    if x_real_ip:
        ip = x_real_ip.strip()
        if ip:
            return ip

    # Check CF-Connecting-IP header (Cloudflare)
    cf_connecting_ip = request.headers.get("cf-connecting-ip")
    if cf_connecting_ip:
        ip = cf_connecting_ip.strip()
        if ip:
            return ip

    # Fallback to request.client.host
    if request.client and request.client.host:
        return request.client.host

    return None
