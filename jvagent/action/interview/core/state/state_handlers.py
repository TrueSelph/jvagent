"""State handlers for interview action.

DEPRECATED: This module is part of the legacy interview flow.
The new target-node architecture in InterviewInteractAction.execute() uses
QuestionWalker.traverse_from_target() which generates directives directly
via the directive_builder. This module is preserved for backward compatibility
but should not be used in new code.

For new implementations, see:
- InterviewInteractAction.execute() - the main entry point
- QuestionWalker.traverse_from_target() - walker-based traversal
- QuestionWalker.on_state_node() - state directive generation
"""

import logging
from typing import TYPE_CHECKING, Any, Optional

from ..session.interview_session import InterviewSession
from .state_machine import InterviewStateMachine
from ..foundation.enums import InterviewState

if TYPE_CHECKING:
    from jvagent.action.interact.interact_walker import InteractWalker
    from jvagent.action.interview.interview_interact_action import InterviewInteractAction

logger = logging.getLogger(__name__)


class StateHandler:
    """DEPRECATED: Handler for generating directives based on interview state.

    This class is part of the legacy flow. The new target-node architecture
    handles directive generation via QuestionWalker.on_state_node() which
    delegates to directive_builder directly.

    Preserved methods:
    - generate_completed_directive: Delegates to directive_builder
    - generate_cancelled_directive: Delegates to directive_builder
    """

    def __init__(self, action: "InterviewInteractAction"):
        """Initialize state handler with action instance.

        Args:
            action: InterviewInteractAction instance
        """
        self.action = action

    async def generate_completed_directive(
        self,
        session: InterviewSession,
        visitor: "InteractWalker"
    ) -> None:
        """Generate directive for COMPLETED state.

        Delegates to DirectiveBuilder.

        Args:
            session: Interview session
            visitor: InteractWalker
        """
        await self.action.directive_builder.generate_completed_directive(session, visitor)

    async def generate_cancelled_directive(
        self,
        session: InterviewSession,
        visitor: "InteractWalker"
    ) -> None:
        """Generate directive for CANCELLED state.

        Delegates to DirectiveBuilder.

        Args:
            session: Interview session
            visitor: InteractWalker
        """
        await self.action.directive_builder.generate_cancelled_directive(session, visitor)
