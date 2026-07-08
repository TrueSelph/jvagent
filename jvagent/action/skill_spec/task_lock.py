"""Shared task-lock types for skill-bound actions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class TaskLockPrep:
    """Optional bound-action output when a task-lock skill turn starts."""

    observations: List[Dict[str, Any]] = field(default_factory=list)
    runtime_ready: Optional[bool] = None
    pending_directive: Optional[str] = None
