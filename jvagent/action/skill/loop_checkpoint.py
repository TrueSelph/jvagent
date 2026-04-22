"""LoopCheckpoint: per-iteration state snapshots persisted to Conversation.context.

Persisting a lightweight checkpoint each iteration means that after an
in-request failure (exception, timeout) the caller can inspect the last known
good state and apply the configured RecoveryPolicy rather than losing all
progress information silently.

Data is stored in ``Conversation.context["_skill_loop_run"]`` as a plain dict
so it is visible to observability tooling and survives conversation saves.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from typing import Any, List, Optional

logger = logging.getLogger(__name__)

_CONTEXT_KEY = "_skill_loop_run"


@dataclass
class LoopCheckpoint:
    """Snapshot of loop state at the start/end of one iteration.

    Attributes:
        iteration: Current iteration number (1-based).
        phase: Loop phase name (see LoopPhase enum values).
        elapsed_seconds: Wall-clock seconds since loop start.
        pending_tool_names: Tool names dispatched in this iteration (if any).
        termination_reason_candidate: Best termination reason known so far.
    """

    iteration: int
    phase: str
    elapsed_seconds: float
    pending_tool_names: List[str] = field(default_factory=list)
    termination_reason_candidate: str = "completed"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "LoopCheckpoint":
        return cls(
            iteration=int(data.get("iteration", 0)),
            phase=str(data.get("phase", "init")),
            elapsed_seconds=float(data.get("elapsed_seconds", 0.0)),
            pending_tool_names=list(data.get("pending_tool_names", [])),
            termination_reason_candidate=str(
                data.get("termination_reason_candidate", "completed")
            ),
        )


class CheckpointStore:
    """Persist and load LoopCheckpoint on a Conversation's context dict.

    Each save overwrites the previous checkpoint; only the most recent
    iteration snapshot is retained (recovery only needs the last known state).

    Args:
        conversation: jvspatial Conversation node with a ``.context`` dict.
    """

    def __init__(self, conversation: Any) -> None:
        self._conversation = conversation

    async def save(self, checkpoint: LoopCheckpoint) -> None:
        """Persist checkpoint to conversation context (does not call conversation.save)."""
        ctx = self._conversation
        if ctx is None:
            return
        context = getattr(ctx, "context", None)
        if not isinstance(context, dict):
            return
        try:
            context[_CONTEXT_KEY] = checkpoint.to_dict()
        except Exception as exc:
            logger.warning("CheckpointStore.save failed: %s", exc)

    def load(self) -> Optional[LoopCheckpoint]:
        """Return the last persisted checkpoint, or None."""
        ctx = self._conversation
        if ctx is None:
            return None
        context = getattr(ctx, "context", None)
        if not isinstance(context, dict):
            return None
        data = context.get(_CONTEXT_KEY)
        if not isinstance(data, dict):
            return None
        try:
            return LoopCheckpoint.from_dict(data)
        except Exception as exc:
            logger.warning("CheckpointStore.load failed: %s", exc)
            return None

    async def clear(self) -> None:
        """Remove the checkpoint from conversation context after a clean finish."""
        ctx = self._conversation
        if ctx is None:
            return
        context = getattr(ctx, "context", None)
        if isinstance(context, dict):
            context.pop(_CONTEXT_KEY, None)
