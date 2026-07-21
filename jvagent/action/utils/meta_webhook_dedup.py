"""Dedup for Meta WhatsApp inbound message ids (wamid).

Meta delivers webhooks at-least-once and retries for up to 7 days. Without dedup,
the agent may reply multiple times to the same user message.

Backend selection (``WHATSAPP_META_WAMID_DEDUP_BACKEND``):

- ``memory`` (default when Redis URL unset): in-process OrderedDict (single worker).
- ``redis``: shared across replicas via ``JVSPATIAL_REDIS_URL`` / ``REDIS_URL``.
- ``auto`` (default): redis when a Redis URL is set, else memory.
"""

from __future__ import annotations

import logging
import os
import time
from collections import OrderedDict
from threading import Lock
from typing import Any, Optional

_DEFAULT_TTL_SECONDS = 86400.0
_DEFAULT_MAX_ENTRIES = 10000
_REDIS_KEY_PREFIX = "jvagent:meta_wamid:"

_seen_wamids: OrderedDict[str, float] = OrderedDict()
_lock = Lock()
_redis_client: Any = None
_redis_init_attempted = False

logger = logging.getLogger(__name__)


def _ttl_seconds() -> float:
    raw = os.environ.get("WHATSAPP_META_WAMID_DEDUP_TTL_SECONDS", "86400")
    try:
        return max(60.0, float(raw))
    except (ValueError, TypeError):
        return _DEFAULT_TTL_SECONDS


def _max_entries() -> int:
    raw = os.environ.get("WHATSAPP_META_WAMID_DEDUP_MAX", "10000")
    try:
        return max(100, int(raw))
    except (ValueError, TypeError):
        return _DEFAULT_MAX_ENTRIES


def _redis_url() -> str:
    return (
        os.environ.get("JVSPATIAL_REDIS_URL")
        or os.environ.get("REDIS_URL")
        or ""
    ).strip()


def _backend() -> str:
    raw = (os.environ.get("WHATSAPP_META_WAMID_DEDUP_BACKEND") or "auto").strip().lower()
    if raw in ("memory", "redis", "auto"):
        return raw
    return "auto"


def _use_redis() -> bool:
    mode = _backend()
    if mode == "memory":
        return False
    if mode == "redis":
        return True
    return bool(_redis_url())


def _get_redis() -> Optional[Any]:
    """Lazy Redis client; returns None if unavailable."""
    global _redis_client, _redis_init_attempted
    if not _use_redis():
        return None
    if _redis_client is not None:
        return _redis_client
    if _redis_init_attempted and _redis_client is None:
        return None
    _redis_init_attempted = True
    url = _redis_url()
    if not url:
        logger.warning(
            "WHATSAPP_META_WAMID_DEDUP_BACKEND=redis but JVSPATIAL_REDIS_URL/REDIS_URL unset; "
            "falling back to in-process dedup"
        )
        return None
    try:
        import redis  # type: ignore[import-untyped]

        _redis_client = redis.from_url(url, decode_responses=True)
        # Fail fast if Redis is down at first use path (ping is cheap).
        _redis_client.ping()
        logger.info("Meta wamid dedup using Redis backend")
        return _redis_client
    except Exception as exc:  # noqa: BLE001 — fall back to memory
        logger.warning(
            "Redis wamid dedup unavailable (%s); falling back to in-process dedup",
            exc,
        )
        _redis_client = None
        return None


def _remember_memory(wamid: str) -> bool:
    now = time.time()
    ttl = _ttl_seconds()
    max_sz = _max_entries()

    with _lock:
        expired = [k for k, exp in _seen_wamids.items() if exp <= now]
        for k in expired:
            del _seen_wamids[k]

        existing = _seen_wamids.get(wamid)
        if existing is not None and existing > now:
            _seen_wamids.move_to_end(wamid)
            return False

        _seen_wamids[wamid] = now + ttl
        while len(_seen_wamids) > max_sz:
            _seen_wamids.popitem(last=False)

    return True


def _remember_redis(client: Any, wamid: str) -> bool:
    """Return True if new (SET NX succeeded), False if duplicate."""
    key = f"{_REDIS_KEY_PREFIX}{wamid}"
    ttl = int(_ttl_seconds())
    # SET NX EX — atomic claim across replicas
    created = client.set(key, "1", nx=True, ex=ttl)
    return bool(created)


def remember_meta_wamid(wamid: str) -> bool:
    """Return True if ``wamid`` is new (and remember it), False if duplicate."""
    key = (wamid or "").strip()
    if not key:
        return True

    client = _get_redis()
    if client is not None:
        try:
            return _remember_redis(client, key)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Redis wamid dedup failed (%s); falling back to in-process for this call",
                exc,
            )
            return _remember_memory(key)

    return _remember_memory(key)


def clear_meta_wamid_cache() -> None:
    """Clear in-process dedup cache (for tests). Does not flush Redis."""
    global _redis_client, _redis_init_attempted
    with _lock:
        _seen_wamids.clear()
    _redis_client = None
    _redis_init_attempted = False
