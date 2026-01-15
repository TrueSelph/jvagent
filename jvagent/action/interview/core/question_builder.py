"""Question builder for interview action.

This module handles building QuestionNode trees from question_index configurations.
"""

import logging
from typing import TYPE_CHECKING, Any, Dict, List

from .question_edge import QuestionEdge
from .question_node import QuestionNode
from .question_walker import QuestionWalker

if TYPE_CHECKING:
    from jvagent.action.interview.interview_interact_action import InterviewInteractAction

logger = logging.getLogger(__name__)


class QuestionBuilder:
    """Builds QuestionNode trees from question_index configurations."""
    
    def __init__(self, action: "InterviewInteractAction"):
        """Initialize question builder with action instance.
        
        Args:
            action: InterviewInteractAction instance
        """
        self.action = action
    
    async def build_question_nodes(self) -> None:
        """Build QuestionNode tree from question_index with conditional branches.

        Creates QuestionNodes and connects them based on branches configuration.
        Supports both linear (no branches) and tree-based (with branches) arrangements.
        """
        # Create all question nodes first
        question_node_map = {}
        for question_config in self.action.question_index:
            question_name = question_config.get("name", "")
            if not question_name:
                continue

            question_node = await QuestionNode.create(
                agent_id=self.action.agent_id,
                state=question_config,
                label=question_name,
            )
            question_node_map[question_name] = question_node
            await self.action.connect(question_node)

        # Now create edges based on branches
        for question_config in self.action.question_index:
            question_name = question_config.get("name", "")
            if not question_name:
                continue

            source_node = question_node_map.get(question_name)
            if not source_node:
                continue

            branches = question_config.get("branches", [])
            default_next = question_config.get("default_next")

            # Create edges for branches
            if branches:
                # Create edges for each branch condition
                for branch in branches:
                    condition = branch.get("condition", {})
                    target_name = branch.get("target")
                    
                    # Skip state targets - they don't have question nodes
                    if target_name and target_name in QuestionWalker.STATE_TARGETS:
                        continue
                    
                    if target_name and target_name in question_node_map:
                        target_node = question_node_map[target_name]
                        # Create edge with condition
                        await source_node.connect(
                            target_node,
                            edge=QuestionEdge,
                            condition=condition
                        )
                
                # If default_next is specified, create an edge to it (represents "else" case)
                # This handles cases where not all branch conditions are covered
                if default_next:
                    # Skip state targets
                    if default_next not in QuestionWalker.STATE_TARGETS and default_next in question_node_map:
                        target_node = question_node_map[default_next]
                        # Create edge without condition (represents default/else case)
                        await source_node.connect(target_node, edge=QuestionEdge)
                else:
                    # No default_next specified - create edge to next question in list
                    # Try to infer complementary condition from existing branches
                    current_idx = next(
                        (i for i, q in enumerate(self.action.question_index) if q.get("name") == question_name),
                        -1
                    )
                    if current_idx >= 0 and current_idx + 1 < len(self.action.question_index):
                        next_question_name = self.action.question_index[current_idx + 1].get("name")
                        if next_question_name and next_question_name in question_node_map:
                            target_node = question_node_map[next_question_name]
                            
                            # Try to infer complementary condition from branches
                            # If we have a branch with condition {"question": "X", "equals": "yes"},
                            # create complementary edge with condition {"question": "X", "equals": "no"}
                            complementary_condition = None
                            if len(branches) == 1:
                                branch = branches[0]
                                branch_condition = branch.get("condition", {})
                                branch_question = branch_condition.get("question")
                                branch_value = branch_condition.get("equals")
                                
                                # If the branch condition is for this same question, infer complementary
                                if branch_question == question_name:
                                    # Check if it's a binary choice (yes/no)
                                    question_config = next(
                                        (q for q in self.action.question_index if q.get("name") == question_name),
                                        None
                                    )
                                    if question_config:
                                        constraints = question_config.get("constraints", {})
                                        options = constraints.get("options", [])
                                        if options and len(options) == 2 and branch_value in options:
                                            # Find the other option
                                            other_value = next((opt for opt in options if opt != branch_value), None)
                                            if other_value:
                                                complementary_condition = {
                                                    "question": question_name,
                                                    "equals": other_value
                                                }
                            
                            # Create edge with complementary condition if inferred, otherwise no condition
                            await source_node.connect(
                                target_node,
                                edge=QuestionEdge,
                                condition=complementary_condition
                            )
            elif default_next:
                # Create edge for default_next
                if default_next in question_node_map:
                    target_node = question_node_map[default_next]
                    await source_node.connect(target_node, edge=QuestionEdge)
            else:
                # Linear flow - connect to next question in list
                current_idx = next(
                    (i for i, q in enumerate(self.action.question_index) if q.get("name") == question_name),
                    -1
                )
                if current_idx >= 0 and current_idx + 1 < len(self.action.question_index):
                    next_question_name = self.action.question_index[current_idx + 1].get("name")
                    if next_question_name and next_question_name in question_node_map:
                        target_node = question_node_map[next_question_name]
                        await source_node.connect(target_node, edge=QuestionEdge)
