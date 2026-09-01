"""Per-event-loop asyncio.Lock registry (serverless warm-start safe).

Module-level ``asyncio.Lock()`` binds to whichever loop first contends it and
raises ``RuntimeError`` on subsequent invocations with a fresh loop. Use
:func:`get_loop_lock` anywhere a process-global lock must survive warm starts.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Dict, Tuple

_locks_guard = threading.Lock()
_loop_locks: Dict[Tuple[int, str], asyncio.Lock] = {}


def get_loop_lock(namespace: str = "default") -> asyncio.Lock:
    """Return an ``asyncio.Lock`` bound to the current running loop."""
    loop = asyncio.get_running_loop()
    lid = id(loop)
    key = (lid, namespace)
    with _locks_guard:
        lock = _loop_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            _loop_locks[key] = lock
        stale = [
            k
            for k, candidate in _loop_locks.items()
            if k[0] != lid
            and getattr(candidate, "_loop", None) is not None
            and candidate._loop.is_closed()  # type: ignore[attr-defined]
        ]
        for stale_key in stale:
            _loop_locks.pop(stale_key, None)
    return lock


__all__ = ["get_loop_lock"]
