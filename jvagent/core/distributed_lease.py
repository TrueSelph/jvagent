"""Generic cluster-wide lease, reused for the bootstrap lock (ADR-0033).

Multiple uvicorn workers / serverless invocations / replicas each run the graph
bootstrap concurrently. On a backend without enforced uniqueness (the default
JSON adapter) their check-then-create paths race and duplicate App / Agents /
Action nodes. Wrapping the graph build in a cluster-wide lease keyed on the app
serializes it: one worker builds, the rest wait then read the finished graph
(bootstrap is idempotent + self-deduping).

Backends are the SAME ones the conversation turn-lock uses (one Redis/DynamoDB
config per deployment). Without either configured this falls back to an
in-process lock, which only serializes within a single worker — cross-process
protection genuinely requires Redis/DynamoDB.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from contextlib import asynccontextmanager
from typing import AsyncIterator, Dict, Optional

from jvagent.core.lease_backend import (
    dynamo_lease,
    dynamo_table,
    lock_ttl_seconds,
    redis_lease,
    redis_url,
)

logger = logging.getLogger(__name__)

_LEASE_PREFIX = "jvagent:lease:"
_REDIS_URL_ENV = "JVAGENT_CONVERSATION_LOCK_REDIS_URL"
_REDIS_TTL_ENV = "JVAGENT_CONVERSATION_LOCK_TTL_SECONDS"
_DYNAMO_TABLE_ENV = "JVAGENT_CONVERSATION_LOCK_DYNAMODB_TABLE"

# Per-key in-process locks (fallback when no distributed backend is configured).
_inproc_locks: Dict[str, asyncio.Lock] = {}
_inproc_guard = threading.Lock()


def _inproc_lock_for(key: str) -> asyncio.Lock:
    """Get or create an in-process asyncio.Lock for *key*.
    
    Used as fallback when no distributed backend is configured.
    """
    with _inproc_guard:
        lock = _inproc_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            _inproc_locks[key] = lock
        return lock


@asynccontextmanager
async def distributed_lease(
    key: str, *, ttl: Optional[int] = None
) -> AsyncIterator[None]:
    """Hold a cluster-wide lease on *key* for the duration of the block.

    Prefers Redis, then DynamoDB, then an in-process lock. The lease is renewed
    by a heartbeat while held (so a slow section can't let it expire) and
    released on exit.
    """
    lease_ttl = (
        ttl if ttl is not None else lock_ttl_seconds(env_var=_REDIS_TTL_ENV)
    )
    url = redis_url(env_var=_REDIS_URL_ENV)
    if url:
        async with redis_lease(key, url, lease_ttl, prefix=_LEASE_PREFIX):
            yield
        return
    table = dynamo_table(env_var=_DYNAMO_TABLE_ENV)
    if table:
        async with dynamo_lease(key, table, lease_ttl, prefix="lease:"):
            yield
        return
    async with _inproc_lock_for(key):
        yield


# Removed _redis_lease and _dynamo_lease — now provided by lease_backend
