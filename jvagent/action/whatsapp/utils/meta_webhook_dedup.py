"""In-process dedup for Meta WhatsApp inbound message ids (wamid).

Meta delivers webhooks at-least-once and retries for up to 7 days. Without dedup,
the agent may reply multiple times to the same user message.
"""

import os
import time
from collections import OrderedDict
from threading import Lock

_DEFAULT_TTL_SECONDS = 86400.0
_DEFAULT_MAX_ENTRIES = 10000

_seen_wamids: OrderedDict[str, float] = OrderedDict()
_lock = Lock()


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


def remember_meta_wamid(wamid: str) -> bool:
    """Return True if ``wamid`` is new (and remember it), False if duplicate."""
    key = (wamid or "").strip()
    if not key:
        return True

    now = time.time()
    ttl = _ttl_seconds()
    max_sz = _max_entries()

    with _lock:
        expired = [k for k, exp in _seen_wamids.items() if exp <= now]
        for k in expired:
            del _seen_wamids[k]

        existing = _seen_wamids.get(key)
        if existing is not None and existing > now:
            _seen_wamids.move_to_end(key)
            return False

        _seen_wamids[key] = now + ttl
        while len(_seen_wamids) > max_sz:
            _seen_wamids.popitem(last=False)

    return True


def clear_meta_wamid_cache() -> None:
    """Clear dedup cache (for tests)."""
    with _lock:
        _seen_wamids.clear()
