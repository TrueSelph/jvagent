"""InteractWalker for traversing InteractActions in the interact subsystem.

This module provides the InteractWalker that serves as the common entry point
for agent interactions, replacing the PersonaAction interact endpoint.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, TYPE_CHECKING
from jvspatial.core import Walker, on_visit
from jvagent.memory.interaction import Interaction

if TYPE_CHECKING:
    from jvagent.action.actions import Actions
    from jvagent.action.interact.base import InteractAction
    from jvagent.core.agent import Agent
    from jvagent.memory.conversation import Conversation
    from jvagent.memory.manager import Memory
    from jvagent.memory.user import User

logger = logging.getLogger(__name__)


class InteractWalker(Walker):
    """Walker that traverses InteractActions for agent interactions.

    InteractWalker is the common entry point for agent interactions. It:
    - Handles user/conversation resolution
    - Creates Interaction node
    - Traverses from Agent -> Actions -> InteractActions
    - Executes top-level InteractActions in weight order (from Actions node)
    - Traverses sub-actions in graph-based arrangement (weight not considered)

    Usage:
        The walker should be spawned directly on the Agent node:
            await walker.spawn(agent)

        This skips the Root -> Agent traversal and starts directly where needed.

    Weight Ordering:
        Weight is only applied at the top tier of the InteractAction graph when
        launching from the Actions node. This is because top-level InteractActions
        are connected in a flat arrangement and there may be multiple top-level
        actions that need ordering.

        Sub-actions (InteractActions connected to other InteractActions) are
        traversed in graph-based arrangement without weight consideration, as
        one InteractAction should lead to another based on the graph structure.
    """

    # Walker state
    agent_id: str = ""
    utterance: str = ""
    channel: str = "default"
    data: Dict[str, Any] = {}
    session_id: Optional[str] = None
    user_id: Optional[str] = None
    interaction: Optional["Interaction"] = None

    @on_visit("Agent")
    async def on_agent(self, here: "Agent") -> None:
        """Visit Agent node and walk to Actions node.

        Args:
            here: The Agent node being visited
        """
        # Initialize interaction if not already done
        if not self.interaction:
            # Get memory from agent
            memory = await here.get_memory()
            if not memory:
                await self.report(
                    {"error": "Agent has no Memory node"}
                )
                return

            # Resolve user and conversation via memory.get_session()
            try:
                user, conversation, resolved_user_id, resolved_session_id = (
                    await memory.get_session(
                        user_id=self.user_id,
                        session_id=self.session_id,
                        channel=self.channel,
                    )
                )
                self.user_id = resolved_user_id
                self.session_id = resolved_session_id

                # Create interaction
                from jvagent.memory.interaction import Interaction

                self.interaction = await conversation.create_interaction(
                    utterance=self.utterance, channel=self.channel
                )

                # Store data on interaction if provided
                if self.data:
                    # Store data in interaction context (if supported)
                    pass  # Interaction may need data attribute

                await self.report(
                    {
                        "interaction_created": {
                            "interaction_id": self.interaction.id,
                            "user_id": self.user_id,
                            "session_id": self.session_id,
                        }
                    }
                )
            except Exception as e:
                await self.report(
                    {"error": f"Failed to initialize interaction: {e}"}
                )
                logger.error(f"Error initializing interaction: {e}", exc_info=True)
                return

        # Get Actions node
        actions_node = await here.get_actions_manager()
        if not actions_node:
            await self.report({"error": "Agent has no Actions node"})
            return

        # Walk to Actions node
        await self.visit(actions_node)

    @on_visit("Actions")
    async def on_actions(self, here: Any) -> None:
        """Visit Actions node and queue top-level InteractActions for traversal.

        Gets all connected InteractActions, filters to enabled ones, sorts by weight
        (weight is only considered at this top tier), and queues them for traversal.
        The @on_visit("InteractAction") handler will process each action with
        depth-first traversal of sub-actions (without weight consideration).

        Args:
            here: The Actions node being visited
        """
        from jvagent.action.interact.base import InteractAction

        # Get all enabled InteractActions (forward direction from Actions node)
        # Filter by enabled=True directly in the query using kwargs
        enabled_actions: List[InteractAction] = await here.nodes(
            node="InteractAction", enabled=True
        )

        if not enabled_actions:
            await self.report({"info": "No enabled InteractActions found"})
            return

        # Sort by weight (negative first, then ascending)
        # Actions with same weight maintain descriptor order (stable sort)
        sorted_actions = sorted(enabled_actions, key=lambda a: a.weight)

        await self.report(
            {
                "interact_actions_found": {
                    "count": len(sorted_actions),
                    "actions": [a.label for a in sorted_actions],
                }
            }
        )

        # Queue actions for traversal (walker will process them via @on_visit)
        await self.visit(sorted_actions)

    @on_visit("InteractAction")
    async def on_interact_action(self, here: "InteractAction") -> None:
        """Visit an InteractAction node: execute it, then traverse sub-actions depth-first.

        This method is automatically called when the walker visits an InteractAction.
        It:
        1. Executes the action's execute() method
        2. Finds connected InteractActions (sub-actions) in graph-based arrangement
        3. Queues them for depth-first traversal (weight is NOT considered for sub-actions)
        4. The walker continues naturally to process queued actions

        Note:
            Sub-actions are traversed in graph-based arrangement without weight
            consideration. Weight is only applied at the top tier when launching
            from the Actions node.

        Args:
            here: The InteractAction node being visited
        """
        if not here.enabled:
            await self.report(
                {
                    "action_skipped": {
                        "action": here.label,
                        "weight": here.weight,
                        "reason": "action is disabled",
                    }
                }
            )
            return

        try:
            # Execute the action
            await here.execute(here, self)
            await self.report(
                {
                    "action_executed": {
                        "action": here.label,
                        "weight": here.weight,
                    }
                }
            )

            # Find connected enabled InteractActions (sub-actions) for depth-first traversal
            # Using forward direction from the current InteractAction
            # Filter by enabled=True directly in the query using kwargs
            enabled_sub_actions: List["InteractAction"] = await here.nodes(
                node="InteractAction", enabled=True
            )

            if enabled_sub_actions:
                # Sub-actions are traversed in graph-based arrangement (no weight sorting)
                # Weight is only considered at the top tier from Actions node
                await self.report(
                    {
                        "sub_actions_found": {
                            "parent": here.label,
                            "count": len(enabled_sub_actions),
                            "actions": [a.label for a in enabled_sub_actions],
                        }
                    }
                )

                # Queue sub-actions at the front for depth-first traversal
                # This ensures sub-actions are processed before sibling actions
                # Traversal follows graph structure, not weight ordering
                await self.add_next(enabled_sub_actions)

        except Exception as e:
            logger.error(
                f"Error processing InteractAction {here.label}: {e}",
                exc_info=True,
            )
            await self.report(
                {
                    "error": f"Failed to process {here.label}",
                    "exception": str(e),
                }
            )
            # Continue to next action (don't raise, let walker continue)

