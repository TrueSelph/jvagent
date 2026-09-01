"""Per-agent Conversation Health State node (day buckets + ambient counters)."""

from __future__ import annotations

import logging
from typing import Any, Dict, Type, TypeVar

from jvspatial.core import Node
from jvspatial.core.annotations import attribute, compound_index

logger = logging.getLogger(__name__)

T = TypeVar("T", bound="ConversationHealthState")


@compound_index(
    [("agent_id", 1)],
    name="conversation_health_state_agent",
    unique=True,
    partial_filter_expression={
        "entity": "ConversationHealthState",
        "context.agent_id": {"$gt": ""},
    },
)
class ConversationHealthState(Node):
    """Mutable per-agent aggregates for Conversation Health Service."""

    agent_id: str = attribute(
        indexed=True,
        default="",
        description="Owning agent id",
    )
    day_buckets: Dict[str, Any] = attribute(
        default_factory=dict,
        description="Per-UTC-day health aggregates and ambient sampling counters",
    )

    @classmethod
    async def get_or_create_for_agent(cls: Type[T], agent_id: str) -> T:
        """Load or create the state node for *agent_id*."""
        if not agent_id:
            raise ValueError("agent_id is required")
        existing = await cls.find_one(
            {
                "entity": "ConversationHealthState",
                "context.agent_id": agent_id,
            }
        )
        if existing:
            return existing  # type: ignore[return-value]

        state = cls(agent_id=agent_id, day_buckets={})
        await state.save()
        # Best-effort edge to Agent for graph navigation
        try:
            from jvagent.core.agent import Agent

            agent = await Agent.get(agent_id)
            if agent is not None:
                try:
                    if not await agent.is_connected_to(state):
                        await agent.connect(state)
                except Exception:
                    logger.debug(
                        "Could not connect ConversationHealthState to agent %s",
                        agent_id,
                        exc_info=True,
                    )
        except Exception:
            logger.debug(
                "Agent lookup failed when creating ConversationHealthState",
                exc_info=True,
            )
        return state
