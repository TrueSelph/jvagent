"""Orphan reattach handlers for graph repair.

Each handler is an async function(context, node, orphan_ids, dry_run, reattach_ctx) -> bool
that returns True if the node was reattached (and node_id was discarded from orphan_ids).

``reattach_ctx`` is an optional pre-built lookup dict produced by
``_build_reattach_context`` in ``graph_repair_job``.  When supplied it avoids
per-orphan ``Memory.find({})`` / ``Agent.find({})`` scans.
"""

from collections import deque
from typing import Any, Callable, Dict, List, Optional, Set

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
    context: Any,
    node: Any,
    orphan_ids: Set[str],
    dry_run: bool,
    reattach_ctx: Optional[Dict[str, Any]] = None,
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
    context: Any,
    node: Any,
    orphan_ids: Set[str],
    dry_run: bool,
    reattach_ctx: Optional[Dict[str, Any]] = None,
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
    context: Any,
    node: Any,
    orphan_ids: Set[str],
    dry_run: bool,
    reattach_ctx: Optional[Dict[str, Any]] = None,
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
    context: Any,
    node: Any,
    orphan_ids: Set[str],
    dry_run: bool,
    reattach_ctx: Optional[Dict[str, Any]] = None,
) -> bool:
    from jvagent.core.agent import Agent

    # Use pre-built list if available to avoid per-orphan Agent.find({}) scan.
    if reattach_ctx and "agents_without_actions" in reattach_ctx:
        agents_without_actions: List[Any] = reattach_ctx["agents_without_actions"]
    else:
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
    context: Any,
    node: Any,
    orphan_ids: Set[str],
    dry_run: bool,
    reattach_ctx: Optional[Dict[str, Any]] = None,
) -> bool:
    from jvagent.core.agent import Agent

    async def has_descendant_memory_data(memory_node: Any) -> bool:
        queue = deque([memory_node])
        seen = {memory_node.id}
        while queue:
            current = queue.popleft()
            try:
                children = await current.nodes(direction="out")
            except Exception:
                continue
            for child in children:
                if child.id in seen:
                    continue
                seen.add(child.id)
                if child.__class__.__name__ in {"User", "Conversation", "Interaction"}:
                    return True
                queue.append(child)
        return False

    if getattr(node, "agent_id", None):
        owning_agent = reattach_ctx.get("agents", {}) if reattach_ctx else None
        # Look up in pre-built dict first
        agent_by_id = {}
        if reattach_ctx:
            agent_by_id = {a.id: a for a in reattach_ctx.get("agents", [])}
        owning_agent = agent_by_id.get(node.agent_id) or await Agent.get(node.agent_id)
        if owning_agent and not await owning_agent.is_connected_to(node):
            if not dry_run:
                await owning_agent.connect(node, direction="out")
            orphan_ids.discard(node.id)
            return True

    incoming_agents = await node.nodes(direction="in", node=Agent)
    if incoming_agents:
        orphan_ids.discard(node.id)
        return True

    # Use pre-built list if available.
    if reattach_ctx and "agents_without_memory" in reattach_ctx:
        agents_without_memory: List[Any] = reattach_ctx["agents_without_memory"]
    else:
        agents_without_memory = []
        for agent in await Agent.find({}):
            mem = await agent.node(node="Memory")
            if not mem:
                agents_without_memory.append(agent)

    if len(agents_without_memory) == 1 and not await agents_without_memory[
        0
    ].is_connected_to(node):
        if not dry_run:
            await agents_without_memory[0].connect(node, direction="out")
        orphan_ids.discard(node.id)
        return True

    if await has_descendant_memory_data(node):
        orphan_ids.discard(node.id)
        return True
    return False


async def _reattach_user(
    context: Any,
    node: Any,
    orphan_ids: Set[str],
    dry_run: bool,
    reattach_ctx: Optional[Dict[str, Any]] = None,
) -> bool:
    from jvagent.memory.manager import Memory

    user_id = getattr(node, "user_id", None)
    if not user_id:
        return False

    incoming_memories = await node.nodes(direction="in", node=Memory)
    if incoming_memories:
        primary = incoming_memories[0]
        if not dry_run and getattr(node, "memory_id", "") != primary.id:
            setattr(node, "memory_id", primary.id)
            await node.save()
        orphan_ids.discard(node.id)
        return True

    # Use pre-built list if available to avoid per-orphan Memory.find({}) scan.
    if reattach_ctx and "memories" in reattach_ctx:
        memories: List[Any] = reattach_ctx["memories"]
    else:
        memories = await Memory.find({})

    preferred_id = getattr(node, "memory_id", "")
    preferred = [m for m in memories if preferred_id and m.id == preferred_id]
    others = sorted(
        [m for m in memories if m.id != preferred_id],
        key=lambda memory: memory.id,
    )
    for memory in preferred + others:
        connected_users = await memory.nodes(node="User")
        if any(u.user_id == user_id and u.id != node.id for u in connected_users):
            continue
        if not await memory.is_connected_to(node):
            if not dry_run:
                setattr(node, "memory_id", memory.id)
                await node.save()
                await memory.connect(node, direction="out")
                # Use atomic increment instead of full recount.
                ctx = await memory.get_context()
                await ctx.atomic_increment(memory.id, "total_users", 1)
            orphan_ids.discard(node.id)
            return True
    return False


async def _reattach_conversation(
    context: Any,
    node: Any,
    orphan_ids: Set[str],
    dry_run: bool,
    reattach_ctx: Optional[Dict[str, Any]] = None,
) -> bool:
    from jvagent.memory.manager import Memory
    from jvagent.memory.user import User

    # Use pre-built list if available.
    if reattach_ctx and "memories" in reattach_ctx:
        memories: List[Any] = reattach_ctx["memories"]
    else:
        memories = await Memory.find({})

    user_id = getattr(node, "user_id", None)
    if not user_id:
        return False
    for memory in memories:
        user = await memory.node(node=User, user_id=user_id)
        if not user:
            continue
        mid = getattr(user, "memory_id", None)
        if mid and mid != memory.id:
            continue
        if not await user.is_connected_to(node):
            if not dry_run:
                await user.connect(node, direction="out")
            orphan_ids.discard(node.id)
            return True

    interactions = await node.nodes(node="Interaction", direction="out")
    if not interactions:
        return False

    sorted_memories = sorted(memories, key=lambda m: m.id)
    target_memory = None
    conversation_memory_id = getattr(node, "memory_id", "")
    if conversation_memory_id:
        target_memory = next(
            (m for m in sorted_memories if m.id == conversation_memory_id), None
        )
    if target_memory is None and len(sorted_memories) == 1:
        target_memory = sorted_memories[0]
    if target_memory is None:
        return False

    recovery_user_id = user_id or f"recovery_{node.id}"
    recovery_user = await target_memory.get_user(
        recovery_user_id, create_if_missing=True
    )
    if not recovery_user:
        return False
    if not await recovery_user.is_connected_to(node):
        if not dry_run:
            await recovery_user.connect(node, direction="out")
    orphan_ids.discard(node.id)
    return True


async def _reattach_action(
    context: Any,
    node: Any,
    orphan_ids: Set[str],
    dry_run: bool,
    reattach_ctx: Optional[Dict[str, Any]] = None,
) -> bool:
    from jvagent.action.base import Action
    from jvagent.core.agent import Agent

    if not isinstance(node, Action):
        return False
    action_agent_id = getattr(node, "agent_id", None)
    if not action_agent_id:
        return False
    # Use pre-built dict if available.
    agent = None
    if reattach_ctx:
        agent_by_id = {a.id: a for a in reattach_ctx.get("agents", [])}
        agent = agent_by_id.get(action_agent_id)
    if agent is None:
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
