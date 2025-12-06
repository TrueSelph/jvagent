"""InteractAction base class for pluggable interact subsystem.

This module provides the InteractAction base class that extends Action and
defines the interface for actions that participate in the interact subsystem.
"""

import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

from jvspatial.core import on_visit
from jvspatial.core.annotations import attribute

from jvagent.action.base import Action

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from jvagent.action.interact.interact_walker import InteractWalker

# Import InteractWalker for @on_visit decorator (needed at class definition time)
# This import is safe because InteractWalker only imports InteractAction for type hints
try:
    from jvagent.action.interact.interact_walker import InteractWalker
except ImportError:
    # If import fails, we'll use string matching in walker
    InteractWalker = None  # type: ignore


class InteractAction(Action, ABC):
    """Base class for actions that participate in the interact subsystem.

    InteractAction extends Action and provides the interface for actions that
    are traversed by InteractWalker. These actions implement execute() which
    is called during walker traversal.

    Implementations should perform evaluation checks at the start of execute()
    and return early if conditions aren't met. This allows flexible, custom
    evaluation logic while keeping the API simple.

    Attributes:
        weight: Execution precedence for top-tier InteractActions only (lower = earlier,
            negative allowed for higher precedence). Weight is only considered when
            InteractActions are launched from the Actions node (top tier). Sub-actions
            (InteractActions connected to other InteractActions) are traversed in
            graph-based arrangement without weight consideration.
    """

    # Weight attribute for execution ordering (top tier only)
    weight: int = attribute(
        default=0,
        description=(
            "Execution precedence for top-tier InteractActions only "
            "(lower = earlier, negative allowed for higher precedence). "
            "Only applied when launching from Actions node. Sub-actions are "
            "traversed in graph-based arrangement without weight consideration."
        ),
    )

    @on_visit(InteractWalker if InteractWalker is not None else "InteractWalker")  # type: ignore
    @abstractmethod
    async def execute(
        self, here: "InteractAction", visitor: "InteractWalker"
    ) -> None:
        """Execute the action's logic on the interaction.

        This method is called when an InteractWalker visits this InteractAction.
        Implementations should perform evaluation checks at the start and return
        early if conditions aren't met.

        Example:
            async def execute(self, here: "InteractAction", visitor: "InteractWalker") -> None:
                # Evaluation checks at the start
                if not self._should_run(visitor):
                    return  # Early return if conditions not met
                
                # Execution logic here
                interaction = visitor.interaction
                # ... perform action logic ...

        Args:
            here: The InteractAction node being visited (this action instance)
            visitor: The InteractWalker visiting this action

        Note:
            Access the Interaction via visitor.interaction
            Access action properties via here (self)
        """
        pass

