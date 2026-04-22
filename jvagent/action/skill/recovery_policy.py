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

    def decide(self, failure: FailureRecord) -> str:
        """Return ``'retry'`` or ``'terminate'``."""
        if not failure.recoverable:
            logger.info(
                "RecoveryPolicy: non-recoverable failure at iter %d/%s → terminate",
                failure.iteration,
                failure.phase,
            )
            return "terminate"

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
            return "terminate"

        self._attempts[key] = used + 1
        logger.info(
            "RecoveryPolicy: retrying iter %d/%s (attempt %d/%d)",
            failure.iteration,
            failure.phase,
            used + 1,
            budget,
        )
        return "retry"

    def is_recoverable(self, exc: Exception) -> bool:
        """Heuristically classify an exception as transient (retryable) or permanent."""
        msg = str(exc).lower()
        return all(marker not in msg for marker in _NON_RECOVERABLE_MARKERS)

    def reset(self) -> None:
        """Clear all retry counters (e.g. between independent runs)."""
        self._attempts.clear()
