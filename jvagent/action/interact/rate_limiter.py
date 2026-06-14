"""Rate limiter and validation for interact endpoint.

This module provides rate limiting by IP and agent_id, as well as
utterance length validation for anonymous requests.
"""

import asyncio
import json
import logging
import time
from collections import defaultdict
from typing import Any, Dict, Optional, Tuple

from jvagent.action.interact.utils.uploads import DEFAULT_UPLOAD_KEYS

logger = logging.getLogger(__name__)

# Cap optional ``data`` JSON on public interact (bytes of serialized payload).
# This bounds *control* data only — media keys are validated separately against
# the larger media cap (base64 uploads legitimately dwarf control data).
DEFAULT_MAX_DATA_JSON_BYTES = 256 * 1024
# Cap the serialized MEDIA portion of ``data`` (inline base64 uploads inflate
# ~33% over raw bytes; this is the total across all media keys).
DEFAULT_MAX_MEDIA_JSON_BYTES = 20 * 1024 * 1024
# Cap each inline base64 upload item before decode (uploads.py enforces too).
DEFAULT_MAX_UPLOAD_ITEM_BYTES = 5 * 1024 * 1024

# ``data`` keys that carry uploaded media (exempt from the control-data cap).
MEDIA_DATA_KEYS = frozenset(DEFAULT_UPLOAD_KEYS)


class InteractRateLimiter:
    """Rate limiter for interact endpoint requests.

    Implements sliding window rate limiting by IP address and agent_id combination.
    Also provides utterance length validation.

    Uses ``asyncio.Lock`` to guard the in-memory timestamp store so concurrent
    requests within a single process are serialised.  Multi-process deployments
    need a shared store (e.g. Redis) — this implementation is adequate for
    single-worker uvicorn or Lambda with at-most-one concurrent invocation.
    """

    def __init__(
        self,
        rate_limit_per_minute: int = 60,
        max_utterance_length: Optional[int] = 2000,
        max_data_json_bytes: Optional[int] = DEFAULT_MAX_DATA_JSON_BYTES,
        max_upload_item_bytes: int = DEFAULT_MAX_UPLOAD_ITEM_BYTES,
        max_media_json_bytes: Optional[int] = DEFAULT_MAX_MEDIA_JSON_BYTES,
    ):
        """Initialize the rate limiter.

        Args:
            rate_limit_per_minute: Maximum requests per minute per IP+agent_id
            max_utterance_length: Maximum characters for utterance (None to disable)
            max_data_json_bytes: Max serialized size of the CONTROL portion of
                ``data`` (media keys excluded; None to disable)
            max_upload_item_bytes: Maximum decoded size per inline upload item
            max_media_json_bytes: Max serialized size of the MEDIA portion of
                ``data`` (the upload keys; None to disable)
        """
        self.rate_limit_per_minute = rate_limit_per_minute
        self.max_utterance_length = max_utterance_length
        self.max_data_json_bytes = max_data_json_bytes
        self.max_upload_item_bytes = max_upload_item_bytes
        self.max_media_json_bytes = max_media_json_bytes
        self._request_timestamps: dict[str, list[float]] = defaultdict(list)
        self._lock = self._new_lock()

    @staticmethod
    def _new_lock() -> asyncio.Lock:
        return asyncio.Lock()

    async def check_rate_limit(self, ip: str, agent_id: str) -> bool:
        """Check if request is within rate limit.

        Args:
            ip: Client IP address
            agent_id: Agent identifier

        Returns:
            True if within rate limit, False if exceeded
        """
        if not ip:
            logger.warning("Rate limit check called without IP address")
            return True

        key = f"{ip}:{agent_id}"
        now = time.time()
        window_start = now - 60.0

        async with self._lock:
            if key in self._request_timestamps:
                self._request_timestamps[key] = [
                    ts for ts in self._request_timestamps[key] if ts > window_start
                ]

            current_count = len(self._request_timestamps[key])
            if current_count >= self.rate_limit_per_minute:
                logger.warning(
                    f"Rate limit exceeded for {key}: {current_count}/{self.rate_limit_per_minute} requests"
                )
                return False

            return True

    async def record_request(self, ip: str, agent_id: str) -> None:
        """Record a request for rate limiting.

        Args:
            ip: Client IP address
            agent_id: Agent identifier
        """
        if not ip:
            return

        key = f"{ip}:{agent_id}"
        now = time.time()

        async with self._lock:
            self._request_timestamps[key].append(now)

            if len(self._request_timestamps) > 1000:
                self._cleanup_old_entries()

    def _cleanup_old_entries(self) -> None:
        """Clean up old entries from the rate limit cache. Caller must hold ``_lock``."""
        now = time.time()
        window_start = now - 60.0
        keys_to_remove = []

        for key, timestamps in self._request_timestamps.items():
            filtered = [ts for ts in timestamps if ts > window_start]
            if filtered:
                self._request_timestamps[key] = filtered
            else:
                keys_to_remove.append(key)

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

    @staticmethod
    def _json_size(obj: Any) -> int:
        return len(json.dumps(obj, separators=(",", ":"), default=str).encode("utf-8"))

    def validate_data_payload(
        self, data: Optional[Dict[str, Any]]
    ) -> Tuple[bool, Optional[str]]:
        """Validate the interact ``data`` dict size — media-aware.

        Uploaded media (the ``MEDIA_DATA_KEYS`` — image_urls, whatsapp_media,
        files, attachments, documents) is base64 and legitimately large, so it is
        validated against ``max_media_json_bytes`` (generous) while the rest of
        ``data`` — control fields the model/flows read — stays bounded by the
        small ``max_data_json_bytes`` cap (abuse protection). Either cap may be
        ``None`` to disable that check.
        """
        if not data:
            return (True, None)
        media = {k: v for k, v in data.items() if k in MEDIA_DATA_KEYS}
        control = {k: v for k, v in data.items() if k not in MEDIA_DATA_KEYS}

        if control and self.max_data_json_bytes is not None:
            try:
                size = self._json_size(control)
            except (TypeError, ValueError) as exc:
                return (False, f"data is not JSON-serializable: {exc}")
            if size > int(self.max_data_json_bytes):
                return (
                    False,
                    "data (excluding uploaded media) exceeds maximum size of "
                    f"{self.max_data_json_bytes} bytes (current: {size} bytes)",
                )

        if media and self.max_media_json_bytes is not None:
            try:
                size = self._json_size(media)
            except (TypeError, ValueError) as exc:
                return (False, f"data is not JSON-serializable: {exc}")
            if size > int(self.max_media_json_bytes):
                return (
                    False,
                    "uploaded media exceeds maximum size of "
                    f"{self.max_media_json_bytes} bytes (current: {size} bytes)",
                )
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
    rate_limit_per_minute: int = 60,
    max_utterance_length: Optional[int] = 2000,
    max_data_json_bytes: Optional[int] = DEFAULT_MAX_DATA_JSON_BYTES,
    max_upload_item_bytes: int = DEFAULT_MAX_UPLOAD_ITEM_BYTES,
    max_media_json_bytes: Optional[int] = DEFAULT_MAX_MEDIA_JSON_BYTES,
) -> None:
    """Initialize the global rate limiter with configuration.

    Args:
        rate_limit_per_minute: Maximum requests per minute per IP+agent_id
        max_utterance_length: Maximum characters for utterance (None to disable)
        max_data_json_bytes: Max serialized CONTROL ``data`` size (media excluded)
        max_upload_item_bytes: Maximum decoded size per inline upload item
        max_media_json_bytes: Max serialized MEDIA ``data`` size (upload keys)
    """
    global _rate_limiter
    _rate_limiter = InteractRateLimiter(
        rate_limit_per_minute=rate_limit_per_minute,
        max_utterance_length=max_utterance_length,
        max_data_json_bytes=max_data_json_bytes,
        max_upload_item_bytes=max_upload_item_bytes,
        max_media_json_bytes=max_media_json_bytes,
    )


def extract_client_ip(request) -> Optional[str]:
    """Extract client IP address from request headers.

    Trust order is configurable via ``JVAGENT_TRUST_PROXY_HEADERS`` env:

    - ``true`` / ``1`` (default for backward compatibility): trust the
      proxy chain headers (X-Forwarded-For, X-Real-IP, CF-Connecting-IP).
      Use this only when jvagent sits behind a trusted reverse proxy
      that overwrites these headers — otherwise a client-supplied
      ``X-Forwarded-For: 1.2.3.4`` will spoof every per-IP rate-limit
      bucket. AUDIT-interact MED-12.
    - ``false`` / ``0``: ignore client-supplied proxy headers; always
      use ``request.client.host``. Safer default for direct-internet
      deployments.

    Order when proxy headers are trusted:
    1. X-Forwarded-For (first IP in comma-separated list)
    2. X-Real-IP
    3. CF-Connecting-IP (Cloudflare)
    4. request.client.host (fallback)

    Args:
        request: FastAPI Request object

    Returns:
        IP address string or None if unavailable
    """
    import os

    trust_proxy = os.environ.get(
        "JVAGENT_TRUST_PROXY_HEADERS", "true"
    ).strip().lower() not in {"false", "0", "no", "off"}

    if not trust_proxy:
        client = getattr(request, "client", None)
        host = getattr(client, "host", None) if client else None
        return host.strip() if isinstance(host, str) and host.strip() else None

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
