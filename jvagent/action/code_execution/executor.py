"""Pluggable code executor for the multitenant code-execution substrate.

An :class:`Executor` runs a shell command inside a confined working directory
and returns captured output. The default :class:`SubprocessExecutor` runs the
command as a child process with POSIX resource limits, a scrubbed environment,
and the cwd confined to the caller's per-user sandbox slice.

SECURITY POSTURE — read this. The subprocess backend is a *pragmatic*, portable
default, NOT a hard security boundary:

- It enforces wall-clock + CPU + memory + file-size + process-count limits and a
  minimal environment (no inherited host secrets).
- It does **not** provide network isolation or a filesystem jail — a determined
  payload can still read world-readable host files or open sockets. Per-user
  *data* isolation comes from the cwd being the caller's own sandbox slice
  (``core.sandbox``), but OS-level containment requires a stronger backend.
- For untrusted or third-party skills, deployments should swap in a
  namespace/container backend (bubblewrap, nsjail, gVisor, a container) that
  satisfies the same :class:`Executor` protocol. Run only trusted skills under
  the subprocess default.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Mapping, Optional, Protocol, runtime_checkable

try:  # POSIX only; absent on Windows.
    import resource as _resource
except ImportError:  # pragma: no cover - non-POSIX
    _resource = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

_DEFAULT_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"


@dataclass(frozen=True)
class ExecRequest:
    """One execution: a shell *command* run in *cwd* under the given limits."""

    command: str
    cwd: str
    timeout: float = 60.0
    max_output_bytes: int = 64_000
    memory_mb: int = 2048
    cpu_seconds: int = 30
    # RLIMIT_NPROC is per-UID (counts ALL of the host UID's processes, not just
    # this child's tree), so a small cap fails on a busy server where the UID is
    # already near the limit ("fork: Resource temporarily unavailable"). Default
    # off (0); real process containment belongs to an isolating backend.
    max_procs: int = 0
    max_file_mb: int = 256
    env: Mapping[str, str] = field(default_factory=dict)
    network: bool = False  # advisory; only enforced by isolating backends


@dataclass(frozen=True)
class ExecResult:
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool
    truncated: bool
    duration_ms: int


@runtime_checkable
class Executor(Protocol):
    """Runs an :class:`ExecRequest` and returns an :class:`ExecResult`."""

    async def run(self, req: ExecRequest) -> ExecResult: ...


def _scrubbed_env(cwd: str, extra: Mapping[str, str]) -> dict:
    """Minimal environment: no inherited host secrets, HOME/TMP inside the cwd."""
    env = {
        "PATH": os.environ.get("PATH", _DEFAULT_PATH),
        "HOME": cwd,
        "TMPDIR": cwd,
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "LC_ALL": os.environ.get("LC_ALL", "C.UTF-8"),
        "PYTHONUNBUFFERED": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    for k, v in (extra or {}).items():
        if k and v is not None:
            env[str(k)] = str(v)
    return env


def _make_preexec(req: ExecRequest):
    """Return a child-side preexec that starts a new session + applies rlimits."""
    if _resource is None:  # pragma: no cover - non-POSIX
        return None

    def _pre() -> None:
        try:
            os.setsid()  # own process group so we can kill the whole tree
        except OSError:
            pass

        def _set(name: str, soft: int) -> None:
            res = getattr(_resource, name, None)
            if res is None:
                return
            try:
                _resource.setrlimit(res, (soft, soft))
            except (ValueError, OSError):
                pass

        if req.cpu_seconds:
            _set("RLIMIT_CPU", req.cpu_seconds)
        if req.memory_mb:
            _set("RLIMIT_AS", req.memory_mb * 1024 * 1024)
        if req.max_file_mb:
            _set("RLIMIT_FSIZE", req.max_file_mb * 1024 * 1024)
        if req.max_procs:
            _set("RLIMIT_NPROC", req.max_procs)

    return _pre


async def _read_capped(stream: Optional[asyncio.StreamReader], cap: int) -> tuple:
    """Read up to *cap* bytes; return (text, truncated)."""
    if stream is None:
        return "", False
    chunks: list = []
    total = 0
    truncated = False
    while True:
        chunk = await stream.read(8192)
        if not chunk:
            break
        if total < cap:
            take = chunk[: max(0, cap - total)]
            chunks.append(take)
            total += len(take)
        if total >= cap:
            truncated = True
            # keep draining so the pipe doesn't block the child
    data = b"".join(chunks)
    return data.decode("utf-8", errors="replace"), truncated


class SubprocessExecutor:
    """Default executor: child process + rlimits + scrubbed env. See module docs."""

    async def run(self, req: ExecRequest) -> ExecResult:
        start = time.monotonic()
        env = _scrubbed_env(req.cwd, req.env)
        try:
            proc = await asyncio.create_subprocess_exec(
                "/bin/bash",
                "-c",
                req.command,
                cwd=req.cwd,
                env=env,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                preexec_fn=_make_preexec(req),
            )
        except (OSError, ValueError) as exc:
            return ExecResult(
                exit_code=127,
                stdout="",
                stderr=f"failed to launch: {exc}",
                timed_out=False,
                truncated=False,
                duration_ms=int((time.monotonic() - start) * 1000),
            )

        cap = max(1, int(req.max_output_bytes))
        timed_out = False
        try:
            out, err = await asyncio.wait_for(
                asyncio.gather(
                    _read_capped(proc.stdout, cap),
                    _read_capped(proc.stderr, cap),
                ),
                timeout=req.timeout,
            )
            await proc.wait()
        except asyncio.TimeoutError:
            timed_out = True
            _kill_tree(proc)
            out, err = ("", False), ("", False)
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except asyncio.TimeoutError:  # pragma: no cover - kill escalation
                pass

        stdout, t1 = out
        stderr, t2 = err
        return ExecResult(
            exit_code=proc.returncode if proc.returncode is not None else -1,
            stdout=stdout,
            stderr=stderr,
            timed_out=timed_out,
            truncated=bool(t1 or t2),
            duration_ms=int((time.monotonic() - start) * 1000),
        )


def _kill_tree(proc: "asyncio.subprocess.Process") -> None:
    import signal

    try:
        pgid = os.getpgid(proc.pid)
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            proc.kill()
        except ProcessLookupError:  # pragma: no cover
            pass


__all__ = ["ExecRequest", "ExecResult", "Executor", "SubprocessExecutor"]
