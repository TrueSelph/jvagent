"""Shared graph traversal helpers for consistent navigation under an Agent subtree."""

from __future__ import annotations

from typing import Any, Optional, Type, TypeVar

from jvspatial.core import Node

T = TypeVar("T", bound=Node)


async def traverse_to_agent(start_node: Node, *, max_hops: int = 48) -> Optional[Any]:
    """Walk incoming edges until an :class:`~jvagent.core.agent.Agent` root is found.

    Used to resolve the owning agent from Memory, User, Conversation, Interaction,
    Actions, Action, or other nodes connected beneath the agent graph.

    Args:
        start_node: Any node in (or below) an agent subgraph.
        max_hops: Safety limit to avoid cycles.

    Returns:
        ``Agent`` instance if reached, else ``None``.
    """
    from jvagent.core.agent import Agent

    current: Any = start_node
    seen: set[str] = set()

    for _ in range(max_hops):
        if current is None:
            return None
        cid = getattr(current, "id", None)
        if cid and cid in seen:
            return None
        if cid:
            seen.add(cid)

        if isinstance(current, Agent):
            return current

        typed = await current.node(direction="in", node=Agent)
        if typed is not None:
            return typed

        parents = await current.nodes(direction="in")
        if not parents:
            return None
        current = parents[0]

    return None


async def traverse_to_node_type(
    start_node: Node,
    target_type: Type[T],
    *,
    max_hops: int = 48,
) -> Optional[T]:
    """Walk incoming edges until a node of *target_type* is found."""
    current: Any = start_node
    seen: set[str] = set()

    for _ in range(max_hops):
        if current is None:
            return None
        cid = getattr(current, "id", None)
        if cid and cid in seen:
            return None
        if cid:
            seen.add(cid)

        if isinstance(current, target_type):
            return current

        typed = await current.node(direction="in", node=target_type)
        if typed is not None:
            return typed

        parents = await current.nodes(direction="in")
        if not parents:
            return None
        current = parents[0]

    return None
