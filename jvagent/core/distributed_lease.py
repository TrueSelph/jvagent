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
import time
import uuid
from contextlib import asynccontextmanager
from typing import AsyncIterator, Dict, Optional

from jvagent.memory.distributed_conversation_lock import (
    _dynamo_table,
    _lease_renew_interval,
    _lock_ttl_seconds,
    _redis_url,
    _run_lease_heartbeat,
)

logger = logging.getLogger(__name__)

_LEASE_PREFIX = "jvagent:lease:"

# Per-key in-process locks (fallback when no distributed backend is configured).
_inproc_locks: Dict[str, asyncio.Lock] = {}
_inproc_guard = threading.Lock()


def _inproc_lock_for(key: str) -> asyncio.Lock:
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
    lease_ttl = ttl if ttl is not None else _lock_ttl_seconds()
    redis_url = _redis_url()
    if redis_url:
        async with _redis_lease(key, redis_url, lease_ttl):
            yield
        return
    table = _dynamo_table()
    if table:
        async with _dynamo_lease(key, table, lease_ttl):
            yield
        return
    async with _inproc_lock_for(key):
        yield


@asynccontextmanager
async def _redis_lease(key: str, redis_url: str, ttl: int) -> AsyncIterator[None]:
    try:
        import redis.asyncio as redis  # type: ignore[import-untyped]
    except ImportError:
        logger.warning("%s-lease: redis not installed; using the in-process lock", key)
        async with _inproc_lock_for(key):
            yield
        return

    rkey = f"{_LEASE_PREFIX}{key}"
    token = str(uuid.uuid4())
    client = redis.from_url(redis_url, decode_responses=True)
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
    heartbeat: Optional["asyncio.Task[None]"] = None
    try:
        while True:
            if await client.set(name=rkey, value=token, nx=True, ex=ttl):
                break
            if asyncio.get_event_loop().time() - start >= max_wait:
                raise TimeoutError(f"lease {key} not acquired within {max_wait}s")
            await asyncio.sleep(delay)
            delay = min(delay * 1.5, 0.5)

        async def _renew() -> None:
            await client.eval(renew_script, 1, rkey, token, str(ttl))

        heartbeat = asyncio.create_task(
            _run_lease_heartbeat(_renew, _lease_renew_interval(ttl), key)
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


@asynccontextmanager
async def _dynamo_lease(key: str, table_name: str, ttl: int) -> AsyncIterator[None]:
    try:
        import boto3  # type: ignore[import-untyped]
        from botocore.exceptions import ClientError  # type: ignore[import-untyped]
    except ImportError:
        logger.warning("%s-lease: boto3 not installed; using the in-process lock", key)
        async with _inproc_lock_for(key):
            yield
        return

    import os

    lock_key = f"lease:{key}"
    token = str(uuid.uuid4())
    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
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
    heartbeat: Optional["asyncio.Task[None]"] = None
    try:
        while True:
            if await asyncio.to_thread(try_acquire):
                break
            if asyncio.get_event_loop().time() - start >= max_wait:
                raise TimeoutError(f"lease {key} not acquired within {max_wait}s")
            await asyncio.sleep(delay)
            delay = min(delay * 1.5, 0.5)

        async def _renew() -> None:
            await asyncio.to_thread(renew)

        heartbeat = asyncio.create_task(
            _run_lease_heartbeat(_renew, _lease_renew_interval(ttl), key)
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
