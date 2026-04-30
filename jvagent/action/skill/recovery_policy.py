"""RecoveryPolicy: deterministic failure handling for mid-loop exceptions.

When the agentic loop catches an exception (model call, tool dispatch, etc.)
it creates a FailureRecord and asks RecoveryPolicy.decide() what to do next.
This keeps failure/recovery logic explicit and testable rather than scattered
through the loop body.

Recovery actions
----------------
``retry``      Re-run the current phase (messages unchanged).  Used for
               transient API errors with remaining retry budget.
``terminate``  Abort the loop and attempt a graceful forced-termination call.
               Used when retries are exhausted or the error is non-recoverable.

Backoff
-------
``decide()`` returns a ``RetryDecision`` that includes an optional
``delay_seconds`` field.  The caller should ``await asyncio.sleep(delay)``
before re-entering the phase.  Backoff is progressive per phase:
  - model_call:    immediate → 2s → 5s
  - tool_dispatch: immediate → 1s
  - default:       immediate
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Exception substrings that are considered non-recoverable.
# "not found" was replaced with more specific variants to avoid false positives
# on transient 404s or "resource not found yet" messages (3.5).
_NON_RECOVERABLE_MARKERS = (
    "invalid api key",
    "permission denied",
    "model not found",
    "resource not found",
    "endpoint not found",
    "content policy",
    "unsupported",
    "invalid model",
)

# Default per-phase retry budgets.
_PHASE_RETRY_BUDGETS: Dict[str, int] = {
    "model_call": 2,
    "tool_dispatch": 1,
    "finalize": 1,
    "default": 1,
}

# Progressive backoff schedule per phase.
# Indexed by (used + 1) — the number of times *this* failure has been retried.
_PHASE_BACKOFF_SCHEDULE: Dict[str, List[float]] = {
    "model_call": [0.0, 2.0, 5.0],
    "tool_dispatch": [0.0, 1.0],
    "finalize": [0.0],
    "default": [0.0],
}


@dataclass
class RetryDecision:
    """Outcome from ``RecoveryPolicy.decide()``.

    Attributes:
        action: ``"retry"`` or ``"terminate"``.
        delay_seconds: Time the caller should sleep before retrying.
    """

    action: str
    delay_seconds: float = 0.0


@dataclass
class FailureRecord:
    """Structured description of a mid-loop failure.

    Attributes:
        iteration: Loop iteration at the time of failure.
        phase: Loop phase name where the failure occurred.
        error: String representation of the exception.
        recoverable: Whether a retry might succeed.
        attempt: How many times this phase has already been retried this iter.
    """

    iteration: int
    phase: str
    error: str
    recoverable: bool
    attempt: int = 0
    extra: Dict[str, Any] = field(default_factory=dict)


class RecoveryPolicy:
    """Stateful policy that tracks retry counts per (iteration, phase).

    Usage::

        policy = RecoveryPolicy()
        # inside the loop:
        failure = FailureRecord(iteration=3, phase="model_call", error=str(e), recoverable=True)
        action = policy.decide(failure)
        if action == "retry":
            continue  # re-run the iteration
        else:
            break     # graceful termination
    """

    def __init__(
        self,
        phase_retry_budgets: Optional[Dict[str, int]] = None,
    ) -> None:
        self._budgets: Dict[str, int] = {
            **_PHASE_RETRY_BUDGETS,
            **(phase_retry_budgets or {}),
        }
        # (iteration, phase) → attempts used
        self._attempts: Dict[str, int] = {}

    def decide(self, failure: FailureRecord) -> RetryDecision:
        """Return a ``RetryDecision`` with ``action`` and optional backoff delay."""
        if not failure.recoverable:
            logger.info(
                "RecoveryPolicy: non-recoverable failure at iter %d/%s → terminate",
                failure.iteration,
                failure.phase,
            )
            return RetryDecision(action="terminate")

        key = f"{failure.iteration}:{failure.phase}"
        used = self._attempts.get(key, 0)
        budget = self._budgets.get(failure.phase, self._budgets["default"])

        if used >= budget:
            logger.info(
                "RecoveryPolicy: retry budget exhausted (%d/%d) at iter %d/%s → terminate",
                used,
                budget,
                failure.iteration,
                failure.phase,
            )
            return RetryDecision(action="terminate")

        self._attempts[key] = used + 1
        # Determine backoff delay from schedule
        schedule = _PHASE_BACKOFF_SCHEDULE.get(
            failure.phase, _PHASE_BACKOFF_SCHEDULE["default"]
        )
        delay = schedule[used] if used < len(schedule) else schedule[-1]

        logger.info(
            "RecoveryPolicy: retrying iter %d/%s (attempt %d/%d, delay %.1fs)",
            failure.iteration,
            failure.phase,
            used + 1,
            budget,
            delay,
        )
        return RetryDecision(action="retry", delay_seconds=delay)

    def is_recoverable(self, exc: Exception) -> bool:
        """Heuristically classify an exception as transient (retryable) or permanent."""
        msg = str(exc).lower()
        return all(marker not in msg for marker in _NON_RECOVERABLE_MARKERS)

    def reset(self) -> None:
        """Clear all retry counters (e.g. between independent runs)."""
        self._attempts.clear()
