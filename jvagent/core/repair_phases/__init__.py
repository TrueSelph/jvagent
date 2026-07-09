"""Repair phase facades.

This package provides stable import points while graph repair logic is
incrementally decomposed from ``graph_repair_job.py``.
"""

from __future__ import annotations

from typing import Any

from .memory import tick_memory_agents, tick_memory_counters
from .types import RepairLimits

__all__ = [
    "RepairLimits",
    "run_repair_session",
    "tick_memory_agents",
    "tick_memory_counters",
]


def __getattr__(name: str) -> Any:
    if name == "run_repair_session":
        from .engine import run_repair_session

        return run_repair_session
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
