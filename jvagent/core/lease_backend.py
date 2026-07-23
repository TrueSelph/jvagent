"""Shared distributed lease backend for Redis and DynamoDB.

Provides acquire/renew/release primitives used by both the generic
distributed_lease (bootstrap lock, ADR-0033) and the conversation turn-lock
(distributed_conversation_lock.py).

Backends:
- Redis: SET NX with TTL + Lua compare-and-delete/compare-and-expire
- DynamoDB: conditional PutItem + conditional DeleteItem
- In-process asyncio.Lock fallback when neither is configured

All lease holders run a heartbeat task that renews the lease before expiry so
long-running operations (multi-step orchestrator turns, slow bootstrap) don't
lapse mid-operation.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from contextlib import asynccontextmanager
from typing import AsyncIterator, Awaitable, Callable, Optional

from jvspatial.env import env

logger = logging.getLogger(__name__)


# =============================================================================
# Configuration helpers
# =============================================================================


def redis_url(*, env_var: str) -> Optional[str]:
    """Resolve Redis URL from env_var, returning None if unset or empty."""
    url = env(env_var, default="").strip()
    return url or None


def dynamo_table(*, env_var: str) -> Optional[str]:
    """Resolve DynamoDB table name from env_var, returning None if unset or empty."""
    name = env(env_var, default="").strip()
    return name or None


def lock_ttl_seconds(*, env_var: str, default: int = 45) -> int:
    """Resolve TTL from env_var, coercing to int >= 5."""
    try:
        return max(5, int(env(env_var, default=str(default))))
    except ValueError:
        return default


def lease_renew_interval(ttl: int) -> float:
    """Heartbeat interval that renews the lease well before it expires.

    A whole turn or bootstrap can exceed the TTL — without renewal the lease
    lapses mid-operation and a second worker acquires it, running concurrently.
    Renewing at ~ttl/3 gives two renewals per TTL window before expiry.
    """
    return max(1.0, ttl / 3.0)


async def run_lease_heartbeat(
    renew: Callable[[], Awaitable[None]], interval: float, key: str
) -> None:
    """Loop: sleep ``interval``, then ``await renew()`` while the lock is held.

    Cancelled by the lock context on release. A failed renewal is logged and the
    loop continues — a transient blip shouldn't drop the lease early.
    """
    while True:
        await asyncio.sleep(interval)
        try:
            await renew()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.debug(
                "lease renew failed for %s (%s)",
                key,
                type(exc).__name__,
            )


# =============================================================================
# Redis lease primitives
# =============================================================================


@asynccontextmanager
async def redis_lease(
    key: str, redis_url_val: str, ttl: int, *, prefix: str = ""
) -> AsyncIterator[None]:
    """Acquire a Redis lease on *key* with TTL, renew via heartbeat, release on exit.

    Args:
        key: Lease key (unprefixed — caller's responsibility if needed)
        redis_url_val: Redis connection URL
        ttl: Lock TTL in seconds
        prefix: Optional prefix to prepend to key (e.g. "jvagent:lease:")

    Falls back to in-process lock if redis is not installed.
    """
    try:
        import redis.asyncio as redis  # type: ignore[import-untyped]
    except ImportError:
        logger.warning("%s-lease: redis not installed; using in-process lock", key)
        from jvagent.core.distributed_lease import _inproc_lock_for

        async with _inproc_lock_for(key):
            yield
        return

    rkey = f"{prefix}{key}"
    token = str(uuid.uuid4())
    client = redis.from_url(redis_url_val, decode_responses=True)
    unlock_script = """
    if redis.call("get", KEYS[1]) == ARGV[1] then
        return redis.call("del", KEYS[1])
    else
        return 0
    end
    """
    renew_script = """
    if redis.call("get", KEYS[1]) == ARGV[1] then
        return redis.call("expire", KEYS[1], ARGV[2])
    else
        return 0
    end
    """
    max_wait = max(ttl + 5, 120)
    start = asyncio.get_event_loop().time()
    delay = 0.05
    attempt = 0
    heartbeat: Optional["asyncio.Task[None]"] = None
    try:
        while True:
            if await client.set(name=rkey, value=token, nx=True, ex=ttl):
                break
            elapsed = asyncio.get_event_loop().time() - start
            if elapsed >= max_wait:
                raise TimeoutError(f"Redis lease {key} not acquired within {max_wait}s")
            attempt += 1
            if attempt % 20 == 0:
                logger.warning(
                    "Still waiting on redis lease for %s (elapsed=%.1fs of %.1fs)",
                    key,
                    elapsed,
                    max_wait,
                )
            await asyncio.sleep(delay)
            delay = min(delay * 1.5, 0.5)

        async def _renew() -> None:
            await client.eval(renew_script, 1, rkey, token, str(ttl))

        heartbeat = asyncio.create_task(
            run_lease_heartbeat(_renew, lease_renew_interval(ttl), key)
        )
        yield
    finally:
        if heartbeat is not None:
            heartbeat.cancel()
            try:
                await heartbeat
            except asyncio.CancelledError:
                pass
        try:
            await client.eval(unlock_script, 1, rkey, token)
        except Exception as exc:
            logger.debug("lease %s release failed (%s)", key, type(exc).__name__)
        try:
            await client.close()
        except Exception:
            pass


# =============================================================================
# DynamoDB lease primitives
# =============================================================================


@asynccontextmanager
async def dynamo_lease(
    key: str, table_name: str, ttl: int, *, prefix: str = ""
) -> AsyncIterator[None]:
    """Acquire a DynamoDB lease on *key* with TTL, renew via heartbeat, release on exit.

    Args:
        key: Lease key (unprefixed)
        table_name: DynamoDB table name
        ttl: Lock TTL in seconds
        prefix: Optional prefix (e.g. "lease:" or "conversation:")

    Falls back to in-process lock if boto3 is not installed.
    """
    try:
        import boto3  # type: ignore[import-untyped]
        from botocore.exceptions import ClientError  # type: ignore[import-untyped]
    except ImportError:
        logger.warning("%s-lease: boto3 not installed; using in-process lock", key)
        from jvagent.core.distributed_lease import _inproc_lock_for

        async with _inproc_lock_for(key):
            yield
        return

    lock_key = f"{prefix}{key}"
    token = str(uuid.uuid4())
    region = env("AWS_REGION", default=None) or env("AWS_DEFAULT_REGION", default=None)
    client = boto3.client("dynamodb", region_name=region or None)

    def try_acquire() -> bool:
        now = int(time.time())
        try:
            client.put_item(
                TableName=table_name,
                Item={
                    "lock_key": {"S": lock_key},
                    "holder": {"S": token},
                    "expires_at": {"N": str(now + ttl)},
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

    def renew() -> None:
        now = int(time.time())
        client.update_item(
            TableName=table_name,
            Key={"lock_key": {"S": lock_key}},
            UpdateExpression="SET expires_at = :e",
            ConditionExpression="holder = :t",
            ExpressionAttributeValues={
                ":e": {"N": str(now + ttl)},
                ":t": {"S": token},
            },
        )

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
                code = e.response.get("Error", {}).get("Code", "Unknown")
                logger.debug("lease %s release failed (%s)", key, code)

    max_wait = max(ttl + 5, 120)
    start = asyncio.get_event_loop().time()
    delay = 0.05
    attempt = 0
    heartbeat: Optional["asyncio.Task[None]"] = None
    try:
        while True:
            if await asyncio.to_thread(try_acquire):
                break
            elapsed = asyncio.get_event_loop().time() - start
            if elapsed >= max_wait:
                raise TimeoutError(
                    f"DynamoDB lease {key} not acquired within {max_wait}s"
                )
            attempt += 1
            if attempt % 20 == 0:
                logger.warning(
                    "Still waiting on DynamoDB lease for %s (elapsed=%.1fs of %.1fs)",
                    key,
                    elapsed,
                    max_wait,
                )
            await asyncio.sleep(delay)
            delay = min(delay * 1.5, 0.5)

        async def _renew() -> None:
            await asyncio.to_thread(renew)

        heartbeat = asyncio.create_task(
            run_lease_heartbeat(_renew, lease_renew_interval(ttl), key)
        )
        yield
    finally:
        if heartbeat is not None:
            heartbeat.cancel()
            try:
                await heartbeat
            except asyncio.CancelledError:
                pass
        await asyncio.to_thread(release)
