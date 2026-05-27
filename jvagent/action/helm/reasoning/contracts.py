"""Reasoning-helm engine contracts: termination reasons, phase enums.

Duplicated from ``jvagent/action/cockpit/contracts.py`` at commit ``4bc6db6``
as part of C-2 (BRIDGE-ROADMAP §C). Zero imports from
``jvagent.action.cockpit`` per the C-strategy hard constraint. Future
revisions of this file may diverge from the standalone-Cockpit source.
"""

from __future__ import annotations

from enum import Enum


class TerminationReason(str, Enum):
    """Canonical termination reasons for the engine think-act-observe loop."""

    COMPLETED = "completed"
    ITER_CAP = "max_iterations"
    TIME_CAP = "timed_out"
    ERROR = "failed"
    STUCK = "stuck_forced"
