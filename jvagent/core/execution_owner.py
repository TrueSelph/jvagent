"""Select the declared execution owner without creating another runtime."""

import hashlib
import json
import os
import socket
import subprocess
import sys
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Any


@lru_cache(maxsize=1)
def _worker_identity(pid: int) -> tuple[str, str]:
    """A fork gets a fresh epoch; host identity includes the Linux PID namespace."""
    namespace = Path("/proc/self/ns/pid")
    boot = Path("/proc/sys/kernel/random/boot_id")
    try:
        if boot.exists():
            boot_identity = boot.read_text().strip()
        elif sys.platform == "darwin":
            boot_identity = subprocess.check_output(
                ["/usr/sbin/sysctl", "-n", "kern.bootsessionuuid"], text=True, timeout=2
            ).strip()
        else:
            raise ValueError("No qualified boot identity")
        uuid.UUID(boot_identity)
        facts = [
            socket.gethostname(),
            boot_identity,
            str(namespace.stat().st_ino) if namespace.exists() else "host-pids",
        ]
    except (OSError, ValueError, subprocess.SubprocessError):
        # Execution remains available; recovery cannot infer local process death.
        return uuid.uuid4().hex, "unverifiable:" + uuid.uuid4().hex
    host = "sha256:" + hashlib.sha256(json.dumps(facts).encode()).hexdigest()
    return uuid.uuid4().hex, host


def native_worker_metadata() -> dict[str, str]:
    """Server-owned facts stored by the official immutable run registration."""
    pid = os.getpid()
    epoch, host = _worker_identity(pid)
    return {"jv_worker_epoch": epoch, "jv_worker_host": host, "jv_worker_pid": str(pid)}


def require_dead_native_owner(metadata: dict[str, str]) -> None:
    """Refuse a live, foreign, historical-unbound or unverifiable local writer.

    This is a local process-death prerequisite, not a distributed lease/fence.
    PID reuse is conservatively refused while that PID exists.
    """
    require_native_local_ownership()
    current = native_worker_metadata()
    epoch, pid = metadata.get("jv_worker_epoch"), metadata.get("jv_worker_pid", "")
    if (
        not isinstance(epoch, str)
        or len(epoch) != 32
        or any(c not in "0123456789abcdef" for c in epoch)
        or metadata.get("jv_worker_host") != current["jv_worker_host"]
    ):
        raise ValueError("Prior native worker identity is unavailable or foreign")
    if epoch == current["jv_worker_epoch"]:
        raise ValueError(
            "Prior native worker may still be active; stop it before recovery"
        )
    if (
        not isinstance(pid, str)
        or not pid.isascii()
        or not pid.isdecimal()
        or not 0 < int(pid) < 2**31
    ):
        raise ValueError("Prior native worker PID is invalid")
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return
    except OSError:
        raise ValueError("Prior native worker termination cannot be verified") from None
    raise ValueError("Prior native worker PID is still alive")


async def uses_native_harness(agent: Any) -> bool:
    """Resolve enabled graph Actions; never fall back after a lookup failure."""
    from jvagent.action.native_harness import NativeHarnessAction

    actions = await agent.get_actions(enabled_only=True)
    native = [action for action in actions if isinstance(action, NativeHarnessAction)]
    if not native:
        return False
    if len(native) != 1 or any(
        action is not native[0] and action.get_manifest().pattern_orchestrator
        for action in actions
    ):
        raise RuntimeError("Configure only one harness execution Action")
    return True


def require_native_local_ownership() -> None:
    """Refuse declared distributed ownership until native fencing is qualified.

    Existing conversation locks remain in force for local admission. Their leases
    alone do not qualify SDK checkpoint, spend, or effect ownership across workers.
    Hosts must not use this development entrypoint in an undeclared worker pool.
    """
    if any(
        os.getenv(name, "").strip()
        for name in (
            "JVAGENT_CONVERSATION_LOCK_REDIS_URL",
            "JVAGENT_CONVERSATION_LOCK_DYNAMODB_TABLE",
        )
    ) or os.getenv("JVAGENT_REQUIRE_DISTRIBUTED_CONVERSATION_LOCK", "").lower() in (
        "true",
        "1",
        "yes",
    ):
        raise RuntimeError("Native distributed execution ownership is not qualified")
    for name in ("WEB_CONCURRENCY", "UVICORN_WORKERS"):
        value = os.getenv(name, "1")
        if not value.isdecimal() or int(value) != 1:
            raise RuntimeError("Native execution requires one declared worker")
