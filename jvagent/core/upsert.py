"""Adapter-agnostic upsert-by-identity lookups (ADR-0033).

Resolves persisted node rows by raw ``context.*`` fields, bypassing jvspatial
``find_one`` subclass filtering. Callers perform create/update while holding the
appropriate lock (``Actions._lock``, distributed bootstrap lease, etc.).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from jvagent.core.jvspatial_compat import find_raw_node_records


def action_record_identity(
    record: Dict[str, Any]
) -> Tuple[Optional[str], Optional[str]]:
    ctx = record.get("context") or {}
    return ctx.get("namespace"), ctx.get("label")


def action_record_archetype(record: Dict[str, Any]) -> str:
    ctx = record.get("context") or {}
    meta = ctx.get("metadata") or {}
    return str(meta.get("class") or record.get("entity") or "")


def action_record_is_singleton(record: Dict[str, Any]) -> bool:
    ctx = record.get("context") or {}
    meta = ctx.get("metadata") or {}
    base_config = meta.get("config") or {}
    overrides = meta.get("config_overrides") or {}
    merged = {**base_config, **overrides}
    return merged.get("singleton", True) is not False


async def find_action_context_records(
    agent_id: str,
    *,
    namespace: Optional[str] = None,
    label: Optional[str] = None,
    archetype: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Return raw action node records for *agent_id* matching optional filters."""
    raw = await find_raw_node_records("agent_id", agent_id)
    matched: List[Dict[str, Any]] = []
    for record in raw:
        ns, lbl = action_record_identity(record)
        if not ns or not lbl:
            continue
        if namespace is not None and ns != namespace:
            continue
        if label is not None and lbl != label:
            continue
        if archetype is not None and action_record_archetype(record) != archetype:
            continue
        matched.append(record)
    return matched


@dataclass(frozen=True)
class UpsertLookupResult:
    """Raw rows matching an identity query (before optional collapse)."""

    records: List[Dict[str, Any]]

    @property
    def keeper_id(self) -> Optional[str]:
        if not self.records:
            return None
        rid = self.records[0].get("id")
        return str(rid) if rid else None


async def upsert_lookup_by_action_identity(
    agent_id: str,
    namespace: str,
    label: str,
) -> UpsertLookupResult:
    """Lookup canonical action identity ``(agent_id, namespace, label)``."""
    records = await find_action_context_records(
        agent_id, namespace=namespace, label=label
    )
    return UpsertLookupResult(records=records)


async def upsert_lookup_by_singleton_archetype(
    agent_id: str,
    archetype: str,
) -> UpsertLookupResult:
    """Lookup singleton action rows by ``(agent_id, metadata.class)``."""
    if not archetype:
        return UpsertLookupResult(records=[])
    records = await find_action_context_records(agent_id, archetype=archetype)
    return UpsertLookupResult(records=records)
