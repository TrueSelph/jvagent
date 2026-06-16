"""Task-runner dispatch registry (ADR-0026 §2.4).

The orchestrator drains the work graph generically: each turn it resolves the top
runnable task (``task_graph.pick_top_runnable``) and dispatches it **by type**. A
runner knows how to *advance* one task type — surface its work, let it make
progress, and report whether it completed, blocked on external input, or merely
advanced. This is the seam that makes the drain standard rather than skill-specific:

- ``SKILL`` tasks are advanced by the orchestrator's own think-act loop (the skill
  turn-lock surface). That path is built in, so ``SKILL`` is always a runnable type
  even though it has no registered runner here.
- Any other type (``action``, ``plan``, a sub-agent delegation, …) is advanced by a
  runner a consumer registers at bootstrap — exactly like the precondition registry,
  the harness never learns what the type *means*.

A runner is ``async (RunContext) -> TaskRunResult``. The harness moves opaque
seed/snapshot payloads for it; it never inspects their contents.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional

# The type the orchestrator advances with its own loop (no registered runner needed).
BUILTIN_SKILL_TYPE = "SKILL"


@dataclass
class RunContext:
    """Everything a runner needs to advance one task (kept duck-typed/minimal)."""

    orchestrator: Any
    visitor: Any
    task: Any  # a TaskHandle
    observations: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class TaskRunResult:
    """What the drain loop does next after a runner advances a task.

    - ``completed`` → the task is done; re-resolve (a parent may now be runnable).
    - ``blocked``   → it needs external input; yield one egress and stay engaged.
    - ``advanced``  → progress made, nothing terminal; continue draining.
    """

    status: str = "advanced"  # "completed" | "blocked" | "advanced"
    observations: List[Dict[str, Any]] = field(default_factory=list)
    directive: Optional[str] = None


TaskRunner = Callable[[RunContext], Awaitable[TaskRunResult]]

_RUNNERS: Dict[str, TaskRunner] = {}


def register_task_runner(task_type: str, runner: TaskRunner) -> None:
    """Bind a task type to a runner. Idempotent (last write wins)."""
    key = _norm(task_type)
    if not key:
        raise ValueError("task_type is required")
    if key == BUILTIN_SKILL_TYPE:
        raise ValueError(
            "SKILL is advanced by the orchestrator loop; it has no external runner"
        )
    if not callable(runner):
        raise ValueError("runner must be callable")
    _RUNNERS[key] = runner


def get_task_runner(task_type: str) -> Optional[TaskRunner]:
    return _RUNNERS.get(_norm(task_type))


def runnable_task_types() -> frozenset:
    """Types the orchestrator can drain: the built-in SKILL type + registered runners.

    ``pick_top_runnable(store, task_types=runnable_task_types())`` therefore only
    surfaces work the orchestrator actually knows how to advance."""
    return frozenset({BUILTIN_SKILL_TYPE, *_RUNNERS.keys()})


def clear_task_runners() -> None:
    """Test helper — reset the registry."""
    _RUNNERS.clear()


def _norm(task_type: Any) -> str:
    return str(task_type or "").strip().upper()
