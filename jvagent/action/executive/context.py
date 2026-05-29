"""TurnContext — the handle passed to every actor tick (Executive and centers).

It bundles the things an actor needs to do its job for one tick: the walker,
the per-turn working memory, the one-shot model budget, the agent, the
capability registry (wired in M2), and thin publish helpers. Keeping this in
one object means a future change to how actors are invoked touches one place.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from jvagent.action.executive.state import ModelBudget, WorkingMemory


@dataclass
class TurnContext:
    """Per-tick context handed to ``Executive`` cognition and ``center.tick``."""

    visitor: Any
    wm: "WorkingMemory"
    model_budget: "ModelBudget"
    action: Any  # the ExecutiveInteractAction orchestrating this turn
    agent: Any = None
    registry: Any = None  # CapabilityRegistry (M2); None until then
    center_names: list = field(default_factory=list)  # activatable worker centers
    center_info: list = field(default_factory=list)  # [{name, purpose}] for routing
    extra: dict = field(default_factory=dict)

    # -- convenience accessors ----------------------------------------

    @property
    def utterance(self) -> str:
        return (getattr(self.visitor, "utterance", "") or "").strip()

    @property
    def interaction(self) -> Any:
        return getattr(self.visitor, "interaction", None)

    def use_model(self) -> None:
        """Acquire the per-tick model budget. Raises ``ModelBudgetExceeded`` on a
        second call within the same tick. Actors MUST call this immediately
        before each language-model call."""
        self.model_budget.acquire()

    async def publish_thought(self, content: str, **kwargs: Any) -> Any:
        """Publish a non-final 'thought' for observability (never final prose)."""
        return await self.action.publish_thought(
            visitor=self.visitor, content=content, **kwargs
        )


__all__ = ["TurnContext"]
