"""Action identity helpers using raw DB records (ADR-0033).

``Action.find_one`` filters by imported subclasses, so existence checks can miss
persisted rows during bootstrap races. Prefer :mod:`jvagent.core.upsert` and
:mod:`jvagent.action.registration` for registration paths.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from jvagent.action.base import Action
from jvagent.core.upsert import find_action_context_records

if TYPE_CHECKING:
    from jvagent.action.actions import Actions

logger = logging.getLogger(__name__)


async def get_raw_action_records_for_agent(agent_id: str) -> List[Dict[str, Any]]:
    return await find_action_context_records(agent_id)


async def find_records_by_identity(
    agent_id: str, namespace: str, label: str
) -> List[Dict[str, Any]]:
    return await find_action_context_records(agent_id, namespace=namespace, label=label)


async def find_records_by_archetype(
    agent_id: str, archetype: str
) -> List[Dict[str, Any]]:
    return await find_action_context_records(agent_id, archetype=archetype)


async def choose_action_keeper_id(
    actions_manager: Actions, records: List[Dict[str, Any]]
) -> Optional[str]:
    """Pick one duplicate to keep: connected, then enabled, then smallest id."""
    from jvspatial.core.entities.node import Node

    scored: List[Tuple[int, int, str]] = []
    for record in records:
        rid = record.get("id")
        if not rid:
            continue
        connected = 0
        enabled = 0
        try:
            node = await Node.get(rid)
            if node is not None:
                if await actions_manager.is_connected_to(node):
                    connected = 1
                enabled = 1 if getattr(node, "enabled", False) else 0
        except Exception:
            pass
        scored.append((-connected, -enabled, rid))

    if not scored:
        return None
    scored.sort()
    return scored[0][2]


async def collapse_duplicate_records(
    actions_manager: Actions, records: List[Dict[str, Any]]
) -> Optional[str]:
    """Keep one action record, delete the rest. Returns the surviving id."""
    if not records:
        return None
    if len(records) == 1:
        return records[0].get("id")

    from jvspatial.core.entities.node import Node

    keeper_id = await choose_action_keeper_id(actions_manager, records)
    if not keeper_id:
        return None

    for record in records:
        rid = record.get("id")
        if not rid or rid == keeper_id:
            continue
        try:
            node = await Node.get(rid)
            if node is None:
                continue
            if await actions_manager.is_connected_to(node):
                await actions_manager.disconnect(node)
            await node.delete(cascade=True)
        except Exception as exc:
            logger.warning(
                "collapse_duplicate_records: failed to remove action %s: %s",
                rid,
                exc,
            )

    try:
        keeper = await Node.get(keeper_id)
        if keeper is not None and not await actions_manager.is_connected_to(keeper):
            await actions_manager.connect(keeper, direction="both")
    except Exception as exc:
        logger.warning(
            "collapse_duplicate_records: failed to reconnect keeper %s: %s",
            keeper_id,
            exc,
        )

    return keeper_id


async def load_action_from_record(record: Dict[str, Any]) -> Optional[Action]:
    rid = record.get("id")
    if not rid:
        return None
    action = await Action.get(rid)
    if action is not None:
        return action
    from jvspatial.core.entities.node import Node

    node = await Node.get(rid)
    return node if isinstance(node, Action) else None
