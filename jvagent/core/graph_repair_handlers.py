"""Orphan reattach handlers for graph repair.

Each handler is an async function(context, node, orphan_ids, dry_run) -> bool
that returns True if the node was reattached (and node_id was discarded from orphan_ids).
"""

from typing import Any, Callable, Dict, Set

_REATTACH_HANDLERS: Dict[str, Callable] = {}


def _register_reattach(entity_name: str):
    """Decorator to register a reattach handler for an entity type."""

    def decorator(fn: Callable):
        _REATTACH_HANDLERS[entity_name] = fn
        return fn

    return decorator


def get_reattach_handler(entity_name: str) -> Callable:
    """Get the reattach handler for an entity type, or None."""
    return _REATTACH_HANDLERS.get(entity_name)


async def _reattach_app(
    context: Any, node: Any, orphan_ids: Set[str], dry_run: bool
) -> bool:
    from jvspatial.core import Root

    root = await Root.get()
    if root and not await root.is_connected_to(node):
        if not dry_run:
            await root.connect(node, direction="out")
        orphan_ids.discard(node.id)
        return True
    return False


async def _reattach_agents(
    context: Any, node: Any, orphan_ids: Set[str], dry_run: bool
) -> bool:
    from jvagent.core.app import App

    app_nodes = await App.find({})
    for app in app_nodes:
        if not await app.is_connected_to(node):
            if not dry_run:
                await app.connect(node, direction="out")
            orphan_ids.discard(node.id)
            return True
    return False


async def _reattach_agent(
    context: Any, node: Any, orphan_ids: Set[str], dry_run: bool
) -> bool:
    from jvagent.core.agents import Agents

    agents_nodes = await Agents.find({})
    for agents in agents_nodes:
        if not await agents.is_connected_to(node):
            if not dry_run:
                await agents.connect(node, direction="out")
            orphan_ids.discard(node.id)
            return True
    return False


async def _reattach_actions(
    context: Any, node: Any, orphan_ids: Set[str], dry_run: bool
) -> bool:
    from jvagent.core.agent import Agent

    agents_without_actions = []
    for a in await Agent.find({}):
        actions_mgr = await a.node(node="Actions")
        if not actions_mgr:
            agents_without_actions.append(a)
    if len(agents_without_actions) == 1 and not await agents_without_actions[
        0
    ].is_connected_to(node):
        if not dry_run:
            await agents_without_actions[0].connect(node, direction="out")
        orphan_ids.discard(node.id)
        return True
    return False


async def _reattach_memory(
    context: Any, node: Any, orphan_ids: Set[str], dry_run: bool
) -> bool:
    from jvagent.core.agent import Agent

    agents_without_memory = []
    for a in await Agent.find({}):
        mem = await a.node(node="Memory")
        if not mem:
            agents_without_memory.append(a)
    if len(agents_without_memory) == 1 and not await agents_without_memory[
        0
    ].is_connected_to(node):
        if not dry_run:
            await agents_without_memory[0].connect(node, direction="out")
        orphan_ids.discard(node.id)
        return True
    return False


async def _reattach_user(
    context: Any, node: Any, orphan_ids: Set[str], dry_run: bool
) -> bool:
    from jvagent.memory.manager import Memory

    user_id = getattr(node, "user_id", None)
    if not user_id:
        return False
    if node.edge_ids:
        return False
    memories = await Memory.find({})
    for memory in memories:
        connected_users = await memory.nodes(node="User")
        if any(u.user_id == user_id for u in connected_users):
            continue
        if not await memory.is_connected_to(node):
            if not dry_run:
                await memory.connect(node, direction="out")
            orphan_ids.discard(node.id)
            return True
    return False


async def _reattach_conversation(
    context: Any, node: Any, orphan_ids: Set[str], dry_run: bool
) -> bool:
    from jvagent.memory.user import User

    user_id = getattr(node, "user_id", None)
    if not user_id:
        return False
    user = await User.find_one({"context.user_id": user_id})
    if user and not await user.is_connected_to(node):
        if not dry_run:
            await user.connect(node, direction="out")
        orphan_ids.discard(node.id)
        return True
    return False


async def _reattach_action(
    context: Any, node: Any, orphan_ids: Set[str], dry_run: bool
) -> bool:
    from jvagent.action.base import Action
    from jvagent.core.agent import Agent

    if not isinstance(node, Action):
        return False
    action_agent_id = getattr(node, "agent_id", None)
    if not action_agent_id:
        return False
    agent = await Agent.get(action_agent_id)
    if not agent:
        return False
    actions_manager = await agent.get_actions_manager()
    if actions_manager and not await actions_manager.is_connected_to(node):
        if not dry_run:
            await actions_manager.connect(node, direction="out")
        orphan_ids.discard(node.id)
        return True
    return False


# Register handlers
_register_reattach("App")(_reattach_app)
_register_reattach("Agents")(_reattach_agents)
_register_reattach("Agent")(_reattach_agent)
_register_reattach("Actions")(_reattach_actions)
_register_reattach("Memory")(_reattach_memory)
_register_reattach("User")(_reattach_user)
_register_reattach("Conversation")(_reattach_conversation)
_register_reattach("Action")(_reattach_action)
