"""GatherInfoWalker for traversing interview nodes.

This module provides the GatherInfoWalker that traverses the ExampleInteractAction
nodes created by GatherInfoInteractAction for interview processes.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING

from jvagent.action.interact.interact_walker import InteractWalker
from jvspatial.core.annotations import attribute
from jvspatial.api import Server, create_server, endpoint
from jvspatial.api.decorators import EndpointField
from jvspatial.core import Node, Root, Walker, on_exit, on_visit

from jvagent.memory.conversation import Conversation
from .data_node import DataNode
from .gather_info_interact_action import GatherInfoInteractAction

logger = logging.getLogger(__name__)

# @endpoint("/api/agents/data_node", methods=["POST"])
class GatherInfoWalker(Walker):
    """Walker that traverses gathered info action nodes.

    This walker is specialized for traversing the sequential chain of
    ExampleInteractAction nodes set up by GatherInfoInteractAction.
    """

    conversation: Conversation = attribute(
        default=None,
        description="Conversation node containing the interview session",
    )
    directive: str = attribute(
        default_factory=str,
        description="Directive to be executed",
    )

    @on_visit(GatherInfoInteractAction)
    async def on_gather_info_interact_action(self, here: GatherInfoInteractAction) -> None:
        """Visit an DataNode node."""
        logger.debug(f"GatherInfoWalker: On {here.label}")
        attached_data_node = await here.node(node="DataNode")
        if attached_data_node:
            logger.debug(f"GatherInfoWalker: Visiting {attached_data_node.label}")
            await self.visit(attached_data_node)


    @on_visit(DataNode)
    async def on_data_node(self, here: DataNode) -> None:
        """Visit a DataNode node."""
        logger.debug(f"GatherInfoWalker: On DataNode {here.label}")
        directive = await here.execute(self)
        if directive:
            self.directive = directive
            return
        attached_data_node = await here.node(node="DataNode")
        if attached_data_node:
            logger.debug(f"GatherInfoWalker: Visiting next node: {attached_data_node.label}")
            await self.visit(attached_data_node)
        return
