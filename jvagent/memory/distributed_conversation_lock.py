"""Cross-process locks for conversation-scoped graph mutations.

``MemoryLockManager`` only serializes coroutines within a single Python process.
For AWS Lambda and other multi-worker deployments, set one of:

- ``JVAGENT_CONVERSATION_LOCK_REDIS_URL`` — Redis ``SET NX`` lease (recommended).
- ``JVAGENT_CONVERSATION_LOCK_DYNAMODB_TABLE`` — DynamoDB conditional Put/Delete
  (requires ``boto3``, table with string partition key ``lock_key``).

If neither is configured, falls back to :class:`~jvagent.memory.lock_manager.MemoryLockManager`.
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional, Tuple

from jvspatial.env import env

from jvagent.core.lease_backend import (
    dynamo_lease,
    dynamo_table,
    lease_renew_interval,
    lock_ttl_seconds,
    redis_lease,
    redis_url,
    run_lease_heartbeat,
)

logger = logging.getLogger(__name__)

# Holder = (conversation_id, holding-task-identity). The task identity MUST be
# part of the guard: ``asyncio.create_task`` copies the current contextvar
# context into the child task, so a background task spawned while the turn holds
# the lock would otherwise inherit ``_lock_holder`` and the reentrancy check
# would report the lock as held — letting that background task (e.g. a
# run_in_background=True action, or Agent.send_proactive_message) mutate the
# interaction chain with NO lock, concurrently with the live turn. Binding the
# holder to the task that acquired it means an inheriting child task (different
# task) does not satisfy the guard and acquires the lock properly, while genuine
# same-task reentrancy still short-circuits. AUDIT-memory HIGH (C8).
_lock_holder: contextvars.ContextVar[Optional[Tuple[str, int]]] = (
    contextvars.ContextVar("conversation_mutation_lock_holder", default=None)
)


def _current_task_id() -> Optional[int]:
    """id() of the running asyncio Task, or None outside a task context."""
    try:
        task = asyncio.current_task()
    except RuntimeError:
        return None
    return id(task) if task is not None else None


def holds_conversation_mutation_lock(conversation_id: str) -> bool:
    """Return True when the CURRENT task already holds *conversation_id*'s turn lock.

    Task-aware: a holder inherited by a child task via context copy does not
    count — only the task that actually acquired the lock does.
    """
    held = _lock_holder.get()
    if held is None:
        return False
    cid, task_id = held
    return cid == conversation_id and task_id == _current_task_id()


_REDIS_URL_ENV = "JVAGENT_CONVERSATION_LOCK_REDIS_URL"
_REDIS_TTL_ENV = "JVAGENT_CONVERSATION_LOCK_TTL_SECONDS"
_DYNAMO_TABLE_ENV = "JVAGENT_CONVERSATION_LOCK_DYNAMODB_TABLE"
_DYNAMO_TTL_ENV = "JVAGENT_CONVERSATION_LOCK_DYNAMODB_TTL_SECONDS"
_LOCK_PREFIX = "jvagent:conversation:"


def warn_missing_distributed_conversation_lock() -> None:
    """Warn when serverless mode runs without a cross-process conversation lock."""
    try:
        from jvspatial import is_serverless_mode
    except ImportError:
        return
    if not is_serverless_mode():
        return
    if redis_url(env_var=_REDIS_URL_ENV) or dynamo_table(env_var=_DYNAMO_TABLE_ENV):
        return
    logger.warning(
        "PRODUCTION SAFETY: serverless mode without %s or %s — concurrent "
        "invocations do not share conversation locks and may fork interaction "
        "chains. Configure Redis or DynamoDB for multi-worker deployments.",
        _REDIS_URL_ENV,
        _DYNAMO_TABLE_ENV,
    )


@asynccontextmanager
async def conversation_mutation_lock(conversation_id: str) -> AsyncIterator[None]:
    """Serialize ``add_interaction`` / chain updates for *conversation_id* cluster-wide."""
    if holds_conversation_mutation_lock(conversation_id):
        yield
        return

    token = _lock_holder.set((conversation_id, _current_task_id()))
    try:
        url = redis_url(env_var=_REDIS_URL_ENV)
        if url:
            ttl = lock_ttl_seconds(env_var=_REDIS_TTL_ENV)
            async with redis_lease(conversation_id, url, ttl, prefix=_LOCK_PREFIX):
                yield
            return

        table = dynamo_table(env_var=_DYNAMO_TABLE_ENV)
        if table:
            ttl = lock_ttl_seconds(env_var=_DYNAMO_TTL_ENV)
            async with dynamo_lease(conversation_id, table, ttl, prefix="conversation:"):
                yield
            return

        from jvagent.memory.lock_manager import get_conversation_lock_manager

        lock_mgr = get_conversation_lock_manager()
        lock = await lock_mgr.acquire(conversation_id)
        async with lock:
            yield
    finally:
        _lock_holder.reset(token)


# Removed _redis_conversation_lock and _dynamo_conversation_lock — now use
# redis_lease / dynamo_lease from lease_backend


@asynccontextmanager
async def _fallback_memory_lock(conversation_id: str) -> AsyncIterator[None]:
    from jvagent.memory.lock_manager import get_conversation_lock_manager

    lock_mgr = get_conversation_lock_manager()
    lock = await lock_mgr.acquire(conversation_id)
    async with lock:
        yield
