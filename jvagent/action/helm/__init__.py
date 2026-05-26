"""Helm base + contracts for the Bridge architecture.

A Helm is an ``Action`` subclass that exposes a single ``step(visitor, bridge_state)``
coroutine returning a ``HelmStepResult`` verb. Bridge orchestrates helms via the
verb set defined in :mod:`jvagent.action.helm.contracts` (see ADR-0007).

This package contains:

- :class:`BaseHelm` — abstract base class for helms.
- Verb dataclasses: :class:`EMIT`, :class:`CONTINUE`, :class:`SHIFT`,
  :class:`DELEGATE`, :class:`YIELD`.
- Supporting types: :class:`ShiftRecord`.
- :class:`StubHelm` — deterministic helm used in tests; not loaded in production.
"""

from jvagent.action.helm.base import BaseHelm
from jvagent.action.helm.contracts import (
    CONTINUE,
    DELEGATE,
    EMIT,
    SHIFT,
    YIELD,
    HelmStepResult,
    HelmVerb,
    ShiftRecord,
)
from jvagent.action.helm.stub_helm import StubHelm

__all__ = [
    "BaseHelm",
    "CONTINUE",
    "DELEGATE",
    "EMIT",
    "SHIFT",
    "YIELD",
    "HelmStepResult",
    "HelmVerb",
    "ShiftRecord",
    "StubHelm",
]
