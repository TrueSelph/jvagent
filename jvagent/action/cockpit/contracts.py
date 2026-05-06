"""Cockpit-local contracts: termination reasons, phase enums, and result types.

Self-contained — no imports from jvagent.action.skill or jvagent.action.router.
"""

from __future__ import annotations

from enum import Enum


class TerminationReason(str, Enum):
    """Canonical termination reasons for the cockpit think-act-observe loop."""

    COMPLETED = "completed"
    ITER_CAP = "max_iterations"
    TIME_CAP = "timed_out"
    ERROR = "failed"
    STUCK = "stuck_forced"
