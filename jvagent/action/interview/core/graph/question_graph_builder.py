"""Question graph builder for interview action.

This module handles building QuestionNode and StateNode graphs from question_graph configurations.
"""

import logging
from typing import TYPE_CHECKING, Any, Dict, List

from ..foundation.enums import InterviewState
from .question_edge import QuestionEdge
from .question_node import QuestionNode
from .state_node import StateNode

if TYPE_CHECKING:
    from jvagent.action.interview.interview_interact_action import (
        InterviewInteractAction,
    )

logger = logging.getLogger(__name__)


class QuestionGraphBuilder:
    """Builds QuestionNode and StateNode graphs from question_graph configurations."""

    def __init__(self, action: "InterviewInteractAction"):
        """Initialize question graph builder with action instance.

        Args:
            action: InterviewInteractAction instance
        """
        self.action = action

    async def build_question_graph(self) -> None:
        """Build QuestionNode and StateNode graph from question_graph with conditional branches.

        Creates QuestionNodes and StateNodes and connects them based on branches configuration.
        Supports both linear (no branches) and tree-based (with branches) arrangements.
        Ensures terminal questions transition to REVIEW state.
        """
        question_graph = self.action._get_question_graph()

        # Create StateNodes for interview states
        state_node_map = {}
        for state in [
            InterviewState.REVIEW,
            InterviewState.COMPLETED,
            InterviewState.CANCELLED,
        ]:
            state_node = await StateNode.create(
                agent_id=self.action.agent_id,
                interview_type=self.action.get_class_name(),
                state_type=state,
                label=state.value.upper(),
            )
            state_node_map[state.value.upper()] = state_node
            await self.action.connect(state_node)

        # Create all question nodes first
        question_node_map = {}
        for question_config in question_graph:
            question_name = question_config.get("name", "")
            if not question_name:
                continue

            question_node = await QuestionNode.create(
                agent_id=self.action.agent_id,
                interview_type=self.action.get_class_name(),
                state=question_config,
                label=question_name,
            )
            question_node_map[question_name] = question_node
            await self.action.connect(question_node)

        def resolve_target(target_name: str):
            """Resolve target name to node (question or state)."""
            if not target_name:
                return None
            # Check if it's a state target
            if target_name.upper() in state_node_map:
                return state_node_map[target_name.upper()]
            # Check if it's a question
            if target_name in question_node_map:
                return question_node_map[target_name]
            return None

        # Now create edges based on branches
        for question_config in question_graph:
            question_name = question_config.get("name", "")
            if not question_name:
                continue

            source_node = question_node_map.get(question_name)
            if not source_node:
                continue

            branches = question_config.get("branches", [])
            default_next = question_config.get("default_next")

            # Create edges for branches (conditional)
            if branches:
                for branch_idx, branch in enumerate(branches):
                    condition = branch.get("condition", {})
                    target_name = branch.get("target")
                    target_node = resolve_target(target_name)
                    if target_node:
                        # Create edge with condition and branch metadata
                        await source_node.connect(
                            target_node,
                            edge=QuestionEdge,
                            condition=condition,
                            branch_index=branch_idx,
                            is_default=False,
                        )

                # IMPORTANT: Also create default edge when branches exist but might not match
                # This ensures the graph has a path when no branch condition matches
                if default_next:
                    # Has default_next, create edge for it (unconditional, for default path)
                    target_node = resolve_target(default_next)
                    if target_node:
                        await source_node.connect(
                            target_node,
                            edge=QuestionEdge,
                            branch_index=-1,
                            is_default=True,
                        )
                else:
                    # No default_next specified, create edge to next question in sequence
                    current_idx = next(
                        (
                            i
                            for i, q in enumerate(question_graph)
                            if q.get("name") == question_name
                        ),
                        -1,
                    )
                    if current_idx >= 0 and current_idx + 1 < len(question_graph):
                        next_question_name = question_graph[current_idx + 1].get("name")
                        if (
                            next_question_name
                            and next_question_name in question_node_map
                        ):
                            target_node = question_node_map[next_question_name]
                            # Create unconditional edge (no condition) for default path
                            await source_node.connect(
                                target_node,
                                edge=QuestionEdge,
                                branch_index=-1,
                                is_default=True,
                            )
            elif default_next:
                # No branches, just default_next
                target_node = resolve_target(default_next)
                if target_node:
                    await source_node.connect(
                        target_node, edge=QuestionEdge, branch_index=-1, is_default=True
                    )
            else:
                # No branches, no default_next - sequential flow
                current_idx = next(
                    (
                        i
                        for i, q in enumerate(question_graph)
                        if q.get("name") == question_name
                    ),
                    -1,
                )
                if current_idx >= 0 and current_idx + 1 < len(question_graph):
                    next_question_name = question_graph[current_idx + 1].get("name")
                    if next_question_name and next_question_name in question_node_map:
                        target_node = question_node_map[next_question_name]
                        await source_node.connect(
                            target_node,
                            edge=QuestionEdge,
                            branch_index=-1,
                            is_default=True,
                        )

        # Ensure terminal questions (those without outgoing edges to other questions) transition to REVIEW
        review_state_node = state_node_map.get(InterviewState.REVIEW.value.upper())
        if review_state_node:
            for question_name, question_node in question_node_map.items():
                # Get all outgoing edges from this question node
                outgoing_question_nodes = await question_node.nodes(
                    direction="out", node=QuestionNode
                )
                outgoing_state_nodes = await question_node.nodes(
                    direction="out", node=StateNode
                )

                # Check if this question is terminal (no outgoing edges to other questions)
                is_terminal = len(outgoing_question_nodes) == 0

                if is_terminal:
                    # Check if it already has a transition to REVIEW
                    has_review_transition = any(
                        state_node.id == review_state_node.id
                        for state_node in outgoing_state_nodes
                    )

                    # If no REVIEW transition exists, add one
                    if not has_review_transition:
                        await question_node.connect(
                            review_state_node,
                            edge=QuestionEdge,
                            branch_index=-1,
                            is_default=True,
                        )

        # Add REVIEW -> COMPLETED edge for auto_confirm traversal
        # This edge is always created regardless of auto_confirm config.
        # When auto_confirm is False, the edge is inert (REVIEW returns a directive).
        # When auto_confirm is True, the walker follows it to COMPLETED.
        completed_state_node = state_node_map.get(
            InterviewState.COMPLETED.value.upper()
        )
        if review_state_node and completed_state_node:
            await review_state_node.connect(
                completed_state_node,
                edge=QuestionEdge,
                branch_index=-1,
                is_default=True,
            )
