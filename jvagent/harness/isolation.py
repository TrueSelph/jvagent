"""Approved skill isolation backends (HP-09).

Subprocess is development containment, not a sandbox. Untrusted script skills
require one of ``gvisor`` / ``firecracker`` / ``nsjail`` with the matching
binary on PATH. This module wraps a command for that binary; it does not
implement a kernel jail itself.
"""

from __future__ import annotations

import shlex
import shutil
from dataclasses import replace
from typing import Mapping

from jvagent.action.code_execution.executor import ExecRequest, ExecResult, Executor

BACKEND_BINS: Mapping[str, str] = {
    "gvisor": "runsc",
    "firecracker": "firecracker",
    "nsjail": "nsjail",
}
APPROVED = frozenset(BACKEND_BINS)


def isolation_binary(backend: str) -> str:
    return BACKEND_BINS.get(backend, backend)


def isolation_available(backend: str) -> bool:
    if backend not in APPROVED:
        return False
    return shutil.which(isolation_binary(backend)) is not None


def wrap_isolated_command(backend: str, req: ExecRequest) -> ExecRequest:
    """Prefix ``req.command`` with the approved backend binary.

    The wrapper is a launch prefix only. Network/fs isolation is whatever that
    binary enforces when present; absence is a refuse, not a subprocess fallback.
    """
    from jvagent.harness.runtime import SkillIsolationRefused

    if backend not in APPROVED:
        raise SkillIsolationRefused(
            f"unapproved isolation backend {backend!r}; subprocess is not a sandbox"
        )
    if not isolation_available(backend):
        raise SkillIsolationRefused(
            f"{backend} binary {isolation_binary(backend)!r} not on PATH"
        )
    bin_name = isolation_binary(backend)
    cwd = shlex.quote(req.cwd)
    inner = shlex.quote(req.command)
    if backend == "nsjail":
        cmd = f"{bin_name} -Mo --cwd {cwd} -- /bin/sh -c {inner}"
    elif backend == "gvisor":
        cmd = f"{bin_name} exec --cwd {cwd} -- /bin/sh -c {inner}"
    else:
        cmd = f"{bin_name} -- {inner}"
    return replace(req, command=cmd)


class IsolatedExecutor:
    """Executor that refuses unless an approved isolation binary is present."""

    def __init__(self, backend: str, inner: Executor) -> None:
        from jvagent.harness.runtime import SkillIsolationRefused

        if backend not in APPROVED:
            raise SkillIsolationRefused(
                f"unapproved isolation backend {backend!r}; subprocess is not a sandbox"
            )
        if not isolation_available(backend):
            raise SkillIsolationRefused(
                f"{backend} binary {isolation_binary(backend)!r} not on PATH"
            )
        self.backend = backend
        self.inner = inner

    async def run(self, req: ExecRequest) -> ExecResult:
        return await self.inner.run(wrap_isolated_command(self.backend, req))


def executor_for_backend(backend: str, inner: Executor) -> Executor:
    if not backend:
        return inner
    return IsolatedExecutor(backend, inner)


__all__ = [
    "BACKEND_BINS",
    "IsolatedExecutor",
    "executor_for_backend",
    "isolation_available",
    "isolation_binary",
    "wrap_isolated_command",
]
