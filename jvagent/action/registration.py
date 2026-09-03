"""Action registration resolution via upsert-by-identity (ADR-0033)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, List, Optional

from jvagent.action.base import Action
from jvagent.core.upsert import (
    UpsertLookupResult,
    upsert_lookup_by_action_identity,
    upsert_lookup_by_singleton_archetype,
)

if TYPE_CHECKING:
    from jvagent.action.actions import Actions

logger = logging.getLogger(__name__)


@dataclass
class ActionRegistrationResolution:
    """Outcome of pre-register identity resolution."""

    existing: Optional[Action]
    archetype: str
    rejected_singleton: bool = False
    stale_records_for_source: Optional[List[dict]] = None


async def _collapse_if_needed(
    actions_manager: Actions,
    lookup: UpsertLookupResult,
) -> UpsertLookupResult:
    from jvagent.action.identity import collapse_duplicate_records

    if len(lookup.records) <= 1:
        return lookup
    keeper_id = await collapse_duplicate_records(actions_manager, lookup.records)
    if not keeper_id:
        return lookup
    refreshed = [r for r in lookup.records if r.get("id") == keeper_id]
    return UpsertLookupResult(records=refreshed or lookup.records[:1])


async def resolve_action_for_registration(
    action: Action,
    actions_manager: Actions,
) -> ActionRegistrationResolution:
    """Resolve whether to create, reuse, or reject an action registration.

    Uses raw DB records (not ``Action.find_one``) and collapses duplicate rows
    for the same identity or singleton archetype before returning.
    """
    from jvagent.action.identity import load_action_from_record

    archetype = action.metadata.get("class", action.get_class_name())
    existing: Optional[Action] = None

    if action.is_singleton:
        singleton_lookup = await upsert_lookup_by_singleton_archetype(
            action.agent_id, archetype
        )
        singleton_lookup = await _collapse_if_needed(actions_manager, singleton_lookup)
        if singleton_lookup.records:
            keeper = singleton_lookup.records[0]
            kns = (keeper.get("context") or {}).get("namespace")
            klbl = (keeper.get("context") or {}).get("label")
            if (kns, klbl) != (action.namespace, action.label):
                logger.warning(
                    "Rejected duplicate singleton action: %s (archetype=%s) "
                    "already registered for agent %s as %s/%s. "
                    "Only one instance per agent allowed.",
                    action.label,
                    archetype,
                    action.agent_id,
                    kns,
                    klbl,
                )
                return ActionRegistrationResolution(
                    existing=None,
                    archetype=archetype,
                    rejected_singleton=True,
                )
            existing = await load_action_from_record(keeper)

    if existing is None:
        identity_lookup = await upsert_lookup_by_action_identity(
            action.agent_id, action.namespace, action.label
        )
        identity_lookup = await _collapse_if_needed(actions_manager, identity_lookup)
        if identity_lookup.records:
            existing = await load_action_from_record(identity_lookup.records[0])

    stale_for_source: Optional[List[dict]] = None
    if action.is_singleton:
        stale_lookup = await upsert_lookup_by_singleton_archetype(
            action.agent_id, archetype
        )
        if stale_lookup.records:
            stale_for_source = list(stale_lookup.records)

    return ActionRegistrationResolution(
        existing=existing,
        archetype=archetype,
        stale_records_for_source=stale_for_source,
    )


async def reconcile_singleton_after_create(
    action: Action,
    actions_manager: Actions,
    *,
    archetype: str,
    action_existed_before: bool,
) -> bool:
    """Post-save race guard for singleton actions.

    Returns True when *action* still exists and ``post_register`` may run.
    Returns False when this node lost a concurrent create race and was removed.
    """
    if not action.is_singleton or action_existed_before:
        return True

    from jvagent.action.identity import collapse_duplicate_records

    lookup = await upsert_lookup_by_singleton_archetype(action.agent_id, archetype)
    if len(lookup.records) <= 1:
        return True

    keeper_id = await collapse_duplicate_records(actions_manager, lookup.records)
    if not keeper_id or action.id == keeper_id:
        return True

    if await actions_manager.is_connected_to(action):
        await actions_manager.disconnect(action)
    actions_manager.registered_count = max(0, actions_manager.registered_count - 1)
    if action.enabled:
        actions_manager.enabled_count = max(0, actions_manager.enabled_count - 1)
    await action.delete(cascade=True)
    await actions_manager.save()
    logger.debug(
        "Singleton %s/%s lost create race for agent %s; kept existing node %s",
        action.namespace,
        action.label,
        action.agent_id,
        keeper_id,
    )
    return False
