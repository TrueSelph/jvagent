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
import os
import time
import uuid
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

logger = logging.getLogger(__name__)

_lock_holder: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "conversation_mutation_lock_holder", default=None
)


def holds_conversation_mutation_lock(conversation_id: str) -> bool:
    """Return True when the current task already holds *conversation_id*'s turn lock."""
    return _lock_holder.get() == conversation_id


_REDIS_URL_ENV = "JVAGENT_CONVERSATION_LOCK_REDIS_URL"
_REDIS_TTL_ENV = "JVAGENT_CONVERSATION_LOCK_TTL_SECONDS"
_DYNAMO_TABLE_ENV = "JVAGENT_CONVERSATION_LOCK_DYNAMODB_TABLE"
_DYNAMO_TTL_ENV = "JVAGENT_CONVERSATION_LOCK_DYNAMODB_TTL_SECONDS"
_LOCK_PREFIX = "jvagent:conversation:"


def _redis_url() -> Optional[str]:
    url = os.environ.get(_REDIS_URL_ENV, "").strip()
    return url or None


def _dynamo_table() -> Optional[str]:
    name = os.environ.get(_DYNAMO_TABLE_ENV, "").strip()
    return name or None


def _lock_ttl_seconds() -> int:
    try:
        return max(5, int(os.environ.get(_REDIS_TTL_ENV, "45")))
    except ValueError:
        return 45


def _dynamo_ttl_seconds() -> int:
    try:
        return max(5, int(os.environ.get(_DYNAMO_TTL_ENV, "45")))
    except ValueError:
        return 45


def warn_missing_distributed_conversation_lock() -> None:
    """Warn when serverless mode runs without a cross-process conversation lock."""
    try:
        from jvspatial import is_serverless_mode
    except ImportError:
        return
    if not is_serverless_mode():
        return
    if _redis_url() or _dynamo_table():
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

    token = _lock_holder.set(conversation_id)
    try:
        redis_url = _redis_url()
        if redis_url:
            async with _redis_conversation_lock(conversation_id, redis_url):
                yield
            return

        dynamo_table = _dynamo_table()
        if dynamo_table:
            async with _dynamo_conversation_lock(conversation_id, dynamo_table):
                yield
            return

        from jvagent.memory.lock_manager import get_conversation_lock_manager

        lock_mgr = get_conversation_lock_manager()
        lock = await lock_mgr.acquire(conversation_id)
        async with lock:
            yield
    finally:
        _lock_holder.reset(token)


@asynccontextmanager
async def _redis_conversation_lock(
    conversation_id: str, redis_url: str
) -> AsyncIterator[None]:
    try:
        import redis.asyncio as redis  # type: ignore[import-untyped]
    except ImportError:
        logger.warning(
            "%s is set but redis is not installed; "
            "install redis>=5 or unset the URL to use the in-process lock",
            _REDIS_URL_ENV,
        )
        async with _fallback_memory_lock(conversation_id):
            yield
        return

    ttl = _lock_ttl_seconds()
    key = f"{_LOCK_PREFIX}{conversation_id}"
    token = str(uuid.uuid4())
    client = redis.from_url(redis_url, decode_responses=True)
    unlock_script = """
    if redis.call("get", KEYS[1]) == ARGV[1] then
        return redis.call("del", KEYS[1])
    else
        return 0
    end
    """

    # AUDIT-memory HIGH-05: bounded wait + exponential backoff + heartbeat
    # logs. Unbounded polling lets a poisoned holder block every other
    # request until its TTL expires.
    max_wait = max(ttl + 5, 60)
    start = asyncio.get_event_loop().time()
    delay = 0.05
    attempt = 0
    try:
        while True:
            acquired = await client.set(name=key, value=token, nx=True, ex=ttl)
            if acquired:
                break
            elapsed = asyncio.get_event_loop().time() - start
            if elapsed >= max_wait:
                raise TimeoutError(
                    f"Redis conversation lock for {conversation_id} not "
                    f"acquired within {max_wait}s"
                )
            attempt += 1
            if attempt % 20 == 0:
                logger.warning(
                    "Still waiting on redis conversation lock for %s "
                    "(elapsed=%.1fs of %.1fs)",
                    conversation_id,
                    elapsed,
                    max_wait,
                )
            await asyncio.sleep(delay)
            delay = min(delay * 1.5, 0.5)
        yield
    finally:
        try:
            await client.eval(unlock_script, 1, key, token)
        except Exception as exc:
            # Redis client exception ``repr`` can echo the connection URL
            # (with credentials) when configured that way. Log only the
            # exception type to keep credentials out of the operator log.
            logger.debug(
                "Redis lock release failed for %s (%s)",
                conversation_id,
                type(exc).__name__,
            )
        try:
            await client.close()
        except Exception:
            pass


@asynccontextmanager
async def _dynamo_conversation_lock(
    conversation_id: str, table_name: str
) -> AsyncIterator[None]:
    try:
        import boto3  # type: ignore[import-untyped]
        from botocore.exceptions import ClientError  # type: ignore[import-untyped]
    except ImportError:
        logger.warning(
            "%s is set but boto3 is not installed; "
            "pip install boto3 or unset the table to use the in-process lock",
            _DYNAMO_TABLE_ENV,
        )
        async with _fallback_memory_lock(conversation_id):
            yield
        return

    ttl_sec = _dynamo_ttl_seconds()
    lock_key = f"conversation:{conversation_id}"
    token = str(uuid.uuid4())
    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
    # One client per acquisition — creating a boto3 client on every poll
    # iteration (and again on release) pays full session/endpoint setup
    # dozens of times under contention. boto3 clients are thread-safe, so
    # sharing it across the to_thread hops below is fine.
    client = boto3.client("dynamodb", region_name=region or None)

    def try_acquire() -> bool:
        now = int(time.time())
        expires = now + ttl_sec
        try:
            client.put_item(
                TableName=table_name,
                Item={
                    "lock_key": {"S": lock_key},
                    "holder": {"S": token},
                    "expires_at": {"N": str(expires)},
                },
                ConditionExpression=(
                    "attribute_not_exists(lock_key) OR expires_at < :now"
                ),
                ExpressionAttributeValues={":now": {"N": str(now)}},
            )
            return True
        except ClientError as e:
            if (
                e.response.get("Error", {}).get("Code")
                != "ConditionalCheckFailedException"
            ):
                raise
            return False

    def release() -> None:
        try:
            client.delete_item(
                TableName=table_name,
                Key={"lock_key": {"S": lock_key}},
                ConditionExpression="holder = :t",
                ExpressionAttributeValues={":t": {"S": token}},
            )
        except ClientError as e:
            if (
                e.response.get("Error", {}).get("Code")
                != "ConditionalCheckFailedException"
            ):
                # AWS error responses may echo request IDs / partial creds.
                # Log only the error code to keep them out of operator logs.
                code = e.response.get("Error", {}).get("Code", "Unknown")
                logger.debug(
                    "DynamoDB lock release failed for %s (%s)",
                    conversation_id,
                    code,
                )

    # AUDIT-memory HIGH-05: same bounded wait + backoff as the Redis path.
    dyn_max_wait = max(ttl_sec + 5, 60)
    dyn_start = asyncio.get_event_loop().time()
    dyn_delay = 0.05
    dyn_attempt = 0
    try:
        while True:
            ok = await asyncio.to_thread(try_acquire)
            if ok:
                break
            elapsed = asyncio.get_event_loop().time() - dyn_start
            if elapsed >= dyn_max_wait:
                raise TimeoutError(
                    f"DynamoDB conversation lock for {conversation_id} not "
                    f"acquired within {dyn_max_wait}s"
                )
            dyn_attempt += 1
            if dyn_attempt % 20 == 0:
                logger.warning(
                    "Still waiting on DynamoDB conversation lock for %s "
                    "(elapsed=%.1fs of %.1fs)",
                    conversation_id,
                    elapsed,
                    dyn_max_wait,
                )
            await asyncio.sleep(dyn_delay)
            dyn_delay = min(dyn_delay * 1.5, 0.5)
        yield
    finally:
        await asyncio.to_thread(release)


@asynccontextmanager
async def _fallback_memory_lock(conversation_id: str) -> AsyncIterator[None]:
    from jvagent.memory.lock_manager import get_conversation_lock_manager

    lock_mgr = get_conversation_lock_manager()
    lock = await lock_mgr.acquire(conversation_id)
    async with lock:
        yield
