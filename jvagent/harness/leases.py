"""Session lease backends (HP-07).

In-process dict is the default. File-backed leases let two processes contend
without Redis. Optional Redis/Dynamo adapters use SET NX / PutItem when those
clients are installed; missing clients raise, they do not silently fall back.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Protocol

DEFAULT_LEASE_TTL_S = 30.0


class LeaseBackend(Protocol):
    def acquire(
        self, session_id: str, worker_id: str, *, ttl_s: float = DEFAULT_LEASE_TTL_S
    ) -> Dict[str, Any]: ...

    def bind(self, session_id: str, worker_id: str, correlation_id: str) -> None: ...

    def renew(
        self, session_id: str, worker_id: str, *, ttl_s: float = DEFAULT_LEASE_TTL_S
    ) -> None: ...

    def release(self, session_id: str, worker_id: str) -> None: ...

    def get(self, session_id: str) -> Optional[Dict[str, Any]]: ...


@dataclass
class InProcessLeaseBackend:
    """Wraps ``HarnessStore.leases``."""

    leases: Dict[str, Dict[str, Any]]

    def acquire(
        self, session_id: str, worker_id: str, *, ttl_s: float = DEFAULT_LEASE_TTL_S
    ) -> Dict[str, Any]:
        now = time.monotonic()
        held = self.leases.get(session_id)
        if held and held["worker_id"] != worker_id and held["expires_at"] > now:
            from jvagent.harness.runtime import SessionBusy

            raise SessionBusy(f"session {session_id} leased by {held['worker_id']}")
        rec = {
            "worker_id": worker_id,
            "expires_at": now + ttl_s,
            "correlation_id": (held or {}).get("correlation_id", ""),
        }
        if held and held["expires_at"] <= now:
            rec["expired_correlation_id"] = held.get("correlation_id") or ""
        self.leases[session_id] = rec
        return rec

    def bind(self, session_id: str, worker_id: str, correlation_id: str) -> None:
        held = self.leases.get(session_id)
        if held and held["worker_id"] == worker_id:
            held["correlation_id"] = correlation_id

    def renew(
        self, session_id: str, worker_id: str, *, ttl_s: float = DEFAULT_LEASE_TTL_S
    ) -> None:
        held = self.leases.get(session_id)
        if not held or held["worker_id"] != worker_id:
            from jvagent.harness.runtime import SessionBusy

            raise SessionBusy(f"session {session_id} not held by {worker_id}")
        held["expires_at"] = time.monotonic() + ttl_s

    def release(self, session_id: str, worker_id: str) -> None:
        held = self.leases.get(session_id)
        if held and held["worker_id"] == worker_id:
            self.leases.pop(session_id, None)

    def get(self, session_id: str) -> Optional[Dict[str, Any]]:
        return self.leases.get(session_id)


class FileLeaseBackend:
    """JSON file + exclusive create. Two OS processes can contend."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text("{}", encoding="utf-8")

    def _load(self) -> Dict[str, Any]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8") or "{}")
        except json.JSONDecodeError:
            return {}

    def _save(self, data: Dict[str, Any]) -> None:
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(tmp, self.path)

    def acquire(
        self, session_id: str, worker_id: str, *, ttl_s: float = DEFAULT_LEASE_TTL_S
    ) -> Dict[str, Any]:
        now = time.time()
        data = self._load()
        held = data.get(session_id)
        if held and held["worker_id"] != worker_id and held["expires_at"] > now:
            from jvagent.harness.runtime import SessionBusy

            raise SessionBusy(f"session {session_id} leased by {held['worker_id']}")
        rec = {
            "worker_id": worker_id,
            "expires_at": now + ttl_s,
            "correlation_id": (held or {}).get("correlation_id", ""),
        }
        if held and held["expires_at"] <= now:
            rec["expired_correlation_id"] = held.get("correlation_id") or ""
        data[session_id] = rec
        self._save(data)
        return rec

    def bind(self, session_id: str, worker_id: str, correlation_id: str) -> None:
        data = self._load()
        held = data.get(session_id)
        if held and held["worker_id"] == worker_id:
            held["correlation_id"] = correlation_id
            self._save(data)

    def renew(
        self, session_id: str, worker_id: str, *, ttl_s: float = DEFAULT_LEASE_TTL_S
    ) -> None:
        data = self._load()
        held = data.get(session_id)
        if not held or held["worker_id"] != worker_id:
            from jvagent.harness.runtime import SessionBusy

            raise SessionBusy(f"session {session_id} not held by {worker_id}")
        held["expires_at"] = time.time() + ttl_s
        self._save(data)

    def release(self, session_id: str, worker_id: str) -> None:
        data = self._load()
        held = data.get(session_id)
        if held and held["worker_id"] == worker_id:
            data.pop(session_id, None)
            self._save(data)

    def get(self, session_id: str) -> Optional[Dict[str, Any]]:
        return self._load().get(session_id)


class RedisLeaseBackend:
    """Optional. Requires ``redis`` package. SET key NX EX."""

    def __init__(self, client: Any, *, prefix: str = "jvagent:lease:") -> None:
        self.client = client
        self.prefix = prefix

    def _key(self, session_id: str) -> str:
        return f"{self.prefix}{session_id}"

    def acquire(
        self, session_id: str, worker_id: str, *, ttl_s: float = DEFAULT_LEASE_TTL_S
    ) -> Dict[str, Any]:
        key = self._key(session_id)
        ok = self.client.set(key, worker_id, nx=True, ex=int(max(ttl_s, 1)))
        if not ok:
            holder = self.client.get(key)
            holder_s = holder.decode() if isinstance(holder, bytes) else holder
            if holder_s != worker_id:
                from jvagent.harness.runtime import SessionBusy

                raise SessionBusy(f"session {session_id} leased by {holder_s}")
        return {
            "worker_id": worker_id,
            "expires_at": time.time() + ttl_s,
            "correlation_id": "",
        }

    def bind(self, session_id: str, worker_id: str, correlation_id: str) -> None:
        self.client.set(self._key(session_id) + ":corr", correlation_id, xx=True)

    def renew(
        self, session_id: str, worker_id: str, *, ttl_s: float = DEFAULT_LEASE_TTL_S
    ) -> None:
        key = self._key(session_id)
        holder = self.client.get(key)
        holder_s = holder.decode() if isinstance(holder, bytes) else holder
        if holder_s != worker_id:
            from jvagent.harness.runtime import SessionBusy

            raise SessionBusy(f"session {session_id} not held by {worker_id}")
        self.client.expire(key, int(max(ttl_s, 1)))

    def release(self, session_id: str, worker_id: str) -> None:
        key = self._key(session_id)
        holder = self.client.get(key)
        holder_s = holder.decode() if isinstance(holder, bytes) else holder
        if holder_s == worker_id:
            self.client.delete(key)

    def get(self, session_id: str) -> Optional[Dict[str, Any]]:
        holder = self.client.get(self._key(session_id))
        if not holder:
            return None
        holder_s = holder.decode() if isinstance(holder, bytes) else holder
        return {"worker_id": holder_s, "correlation_id": "", "expires_at": 0}


class DynamoLeaseBackend:
    """Optional. Requires a DynamoDB-like client with put_item/get_item/delete_item.

    Missing client is a raise, not a silent in-process fallback.
    """

    def __init__(self, client: Any, *, table: str = "jvagent-leases") -> None:
        self.client = client
        self.table = table

    def acquire(
        self, session_id: str, worker_id: str, *, ttl_s: float = DEFAULT_LEASE_TTL_S
    ) -> Dict[str, Any]:
        expires = int(time.time() + ttl_s)
        existing = self.client.get_item(
            TableName=self.table, Key={"session_id": {"S": session_id}}
        )
        item = (existing or {}).get("Item") or {}
        holder = ((item.get("worker_id") or {}).get("S")) or ""
        exp = int(((item.get("expires_at") or {}).get("N")) or 0)
        now = int(time.time())
        if holder and holder != worker_id and exp > now:
            from jvagent.harness.runtime import SessionBusy

            raise SessionBusy(f"session {session_id} leased by {holder}")
        rec = {
            "worker_id": worker_id,
            "expires_at": float(expires),
            "correlation_id": ((item.get("correlation_id") or {}).get("S")) or "",
        }
        if holder and exp <= now:
            rec["expired_correlation_id"] = rec["correlation_id"]
        self.client.put_item(
            TableName=self.table,
            Item={
                "session_id": {"S": session_id},
                "worker_id": {"S": worker_id},
                "expires_at": {"N": str(expires)},
                "correlation_id": {"S": rec["correlation_id"]},
            },
        )
        return rec

    def bind(self, session_id: str, worker_id: str, correlation_id: str) -> None:
        held = self.get(session_id)
        if held and held["worker_id"] == worker_id:
            held["correlation_id"] = correlation_id
            self.client.put_item(
                TableName=self.table,
                Item={
                    "session_id": {"S": session_id},
                    "worker_id": {"S": worker_id},
                    "expires_at": {"N": str(int(held.get("expires_at") or 0))},
                    "correlation_id": {"S": correlation_id},
                },
            )

    def renew(
        self, session_id: str, worker_id: str, *, ttl_s: float = DEFAULT_LEASE_TTL_S
    ) -> None:
        held = self.get(session_id)
        if not held or held["worker_id"] != worker_id:
            from jvagent.harness.runtime import SessionBusy

            raise SessionBusy(f"session {session_id} not held by {worker_id}")
        held["expires_at"] = time.time() + ttl_s
        self.bind(session_id, worker_id, held.get("correlation_id") or "")

    def release(self, session_id: str, worker_id: str) -> None:
        held = self.get(session_id)
        if held and held["worker_id"] == worker_id:
            self.client.delete_item(
                TableName=self.table, Key={"session_id": {"S": session_id}}
            )

    def get(self, session_id: str) -> Optional[Dict[str, Any]]:
        existing = self.client.get_item(
            TableName=self.table, Key={"session_id": {"S": session_id}}
        )
        item = (existing or {}).get("Item") or {}
        if not item:
            return None
        return {
            "worker_id": ((item.get("worker_id") or {}).get("S")) or "",
            "correlation_id": ((item.get("correlation_id") or {}).get("S")) or "",
            "expires_at": float(((item.get("expires_at") or {}).get("N")) or 0),
        }


def lease_backend_for(
    kind: str,
    *,
    leases: Optional[Dict[str, Dict[str, Any]]] = None,
    path: Optional[Path] = None,
    redis_client: Any = None,
    dynamo_client: Any = None,
) -> LeaseBackend:
    if kind in ("", "memory", "inprocess"):
        return InProcessLeaseBackend(leases if leases is not None else {})
    if kind == "file":
        if path is None:
            raise ValueError("file lease backend requires path")
        return FileLeaseBackend(path)
    if kind == "redis":
        if redis_client is None:
            raise ValueError(
                "redis lease backend requires a client; no silent fallback"
            )
        return RedisLeaseBackend(redis_client)
    if kind in ("dynamo", "dynamodb"):
        if dynamo_client is None:
            raise ValueError(
                "dynamo lease backend requires a client; no silent fallback"
            )
        return DynamoLeaseBackend(dynamo_client)
    raise ValueError(f"unknown lease backend {kind!r}")


__all__ = [
    "DEFAULT_LEASE_TTL_S",
    "DynamoLeaseBackend",
    "FileLeaseBackend",
    "InProcessLeaseBackend",
    "LeaseBackend",
    "RedisLeaseBackend",
    "lease_backend_for",
]
