"""Target resolution for interview flow.

Determines session.target_node based on intent, state, and interview progress.
Extracted from InterviewInteractAction for testability and separation of concerns.
"""

from typing import TYPE_CHECKING, Optional

from ..foundation.enums import Intent, InterviewState
from ..session.interview_session import InterviewSession
from ..utils.session_utils import get_graph_order

if TYPE_CHECKING:
    from jvagent.action.interact.interact_walker import InteractWalker
    from jvagent.action.interview.interview_interact_action import (
        InterviewInteractAction,
    )


class TargetResolver:
    """Resolves session.target_node based on intent and interview state.

    Rules (evaluated in order):
    - CANCELLATION intent → CancelledStateNode
    - CONFIRMATION in REVIEW state → CompletedStateNode
    - UPDATE intent → First question node (re-evaluate from beginning)
    - ACTIVE + all answered → ReviewStateNode
    - ACTIVE + DECLINE → Keep current target_node (QuestionNode handles logic)
    - ACTIVE + NONE → First unanswered (if target not set)
    - ACTIVE + SUBMISSION → Last answered question node
    - REVIEW + other → ReviewStateNode (re-show summary)
    - Fallback → First question node
    """

    def __init__(self, action: "InterviewInteractAction"):
        """Initialize with the interview action.

        Args:
            action: InterviewInteractAction instance for node lookups
        """
        self.action = action

    async def resolve(
        self,
        session: InterviewSession,
        intent: Intent,
        visitor: Optional["InteractWalker"] = None,
    ) -> None:
        """Determine and set session.target_node based on intent, state, and progress.

        Args:
            session: Interview session
            intent: Detected user intent
            visitor: Optional InteractWalker for branch function evaluation
        """
        from ..graph.question_path_walker import QuestionPathWalker

        current_state = session.state
        changed = False

        # CANCELLATION — always goes to cancelled state
        if intent == Intent.CANCELLATION:
            node = await self.action.get_state_node(InterviewState.CANCELLED)
            session.target_node = node.id if node else None
            changed = True

        # CONFIRMATION in REVIEW state — goes to completed
        elif intent == Intent.CONFIRMATION and current_state == InterviewState.REVIEW:
            node = await self.action.get_state_node(InterviewState.COMPLETED)
            session.target_node = node.id if node else None
            changed = True

        # UPDATE or pending update queue — resolve to earliest queue entry
        elif intent == Intent.UPDATE or (
            session.update_queue and intent in (Intent.SUBMISSION, Intent.NONE)
        ):
            if session.update_queue:
                earliest_field = session.update_queue[0]["field"]
                first_node = await self.action._get_first_question_node(session)
                next_unanswered = await QuestionPathWalker.find_next_target(
                    session, first_node, visitor, self.action
                )
                if next_unanswered:
                    graph_order = get_graph_order(session.question_graph)
                    first_unanswered_idx = graph_order.get(
                        (
                            next_unanswered.state.get("name", next_unanswered.label)
                            if hasattr(next_unanswered, "state")
                            else next_unanswered.label
                        ),
                        999,
                    )
                    earliest_queue_idx = graph_order.get(earliest_field, 999)

                    if first_unanswered_idx < earliest_queue_idx:
                        session.target_node = first_node.id if first_node else None
                    else:
                        node = await self.action._get_question_node(
                            earliest_field, session
                        )
                        session.target_node = node.id if node else None
                else:
                    node = await self.action._get_question_node(earliest_field, session)
                    session.target_node = node.id if node else None
            else:
                first_question = await self.action._get_first_question_node(session)
                session.target_node = first_question.id if first_question else None
            session.state = InterviewState.ACTIVE
            changed = True

        # Handle ACTIVE state intents
        elif current_state == InterviewState.ACTIVE:
            first_node = await self.action._get_first_question_node(session)
            next_unanswered = await QuestionPathWalker.find_next_target(
                session, first_node, visitor, self.action
            )
            if not next_unanswered:
                node = await self.action.get_state_node(InterviewState.REVIEW)
                session.target_node = node.id if node else None
                changed = True
            elif intent == Intent.DECLINE:
                if not session.target_node:
                    session.target_node = next_unanswered.id
                    changed = True
            elif intent == Intent.NONE:
                if not session.target_node:
                    session.target_node = next_unanswered.id
                    changed = True
            elif intent == Intent.SUBMISSION:
                # Spawn at last answered question so directive override runs, then continue
                if session.update_queue:
                    last_field = session.update_queue[-1]["field"]
                    node = await self.action._get_question_node(last_field, session)
                    session.target_node = (
                        node.id if node else (first_node.id if first_node else None)
                    )
                else:
                    session.target_node = first_node.id if first_node else None
                changed = True
            else:
                first_question = await self.action._get_first_question_node(session)
                session.target_node = first_question.id if first_question else None
                changed = True

        # Handle REVIEW state (non-CONFIRMATION, non-UPDATE)
        elif current_state == InterviewState.REVIEW:
            node = await self.action.get_state_node(InterviewState.REVIEW)
            session.target_node = node.id if node else None
            changed = True

        # Fallback — start from first question
        else:
            first_question = await self.action._get_first_question_node(session)
            session.target_node = (
                first_question.id if first_question else self.action.id
            )
            changed = True

        if changed:
            await session.save()
