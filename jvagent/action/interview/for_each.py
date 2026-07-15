"""for_each expansion — per-item subpart field iteration state and helpers."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from .session import InterviewSession
from .spec import FieldDef, InterviewSpec

logger = logging.getLogger(__name__)

FOR_EACH_CONTEXT_KEY = "for_each"

STATUS_ACTIVE = "active"
STATUS_COMPLETE = "complete"
STATUS_SKIPPED = "skipped"

_ORDINALS = {
    1: "first",
    2: "second",
    3: "third",
    4: "fourth",
    5: "fifth",
    6: "sixth",
    7: "seventh",
    8: "eighth",
    9: "ninth",
    10: "tenth",
    11: "eleventh",
    12: "twelfth",
}


def _ordinal(index: int) -> str:
    return _ORDINALS.get(index, f"{index}th")


def _singularize_key(key: str) -> str:
    words = key.replace("_", " ").split()
    result = []
    for word in words:
        if word.endswith("ies") and len(word) > 3:
            result.append(word[:-3] + "y")
        elif (
            word.endswith("shes")
            or word.endswith("ches")
            or word.endswith("xes")
            or word.endswith("sses")
        ):
            result.append(word[:-2])
        elif word.endswith("ses") and not word.endswith("sses"):
            result.append(word[:-1])
        elif word.endswith("s") and not word.endswith("ss"):
            result.append(word[:-1])
        else:
            result.append(word)
    return " ".join(result)


@dataclass(frozen=True)
class ActiveForEach:
    parent_key: str
    parent_fdef: FieldDef
    state: Dict[str, Any]


def get_for_each_store(session: InterviewSession) -> Dict[str, Any]:
    ctx = session.context
    if not isinstance(ctx, dict):
        return {}
    store = ctx.get(FOR_EACH_CONTEXT_KEY)
    return store if isinstance(store, dict) else {}


def child_keys_for_parent(fdef: FieldDef) -> List[str]:
    if not fdef.for_each:
        return []
    return [c.key for c in fdef.for_each.fields]


def clear_child_scratch(session: InterviewSession, child_keys: List[str]) -> None:
    for key in child_keys:
        session.fields.pop(key, None)
        session.skipped_fields.discard(key)


def apply_staged_fields(
    session: InterviewSession, child_keys: List[str], item_id: str
) -> bool:
    if not isinstance(session.context, dict):
        return False
    stage_store = session.context.get("_for_each_staged")
    if not isinstance(stage_store, dict):
        return False
    item_staged = stage_store.pop(item_id, None)
    if not item_staged or not isinstance(item_staged, dict):
        return False
    applied = False
    for ck in child_keys:
        if (
            ck in item_staged
            and not session.has_field(ck)
            and not session.is_skipped(ck)
        ):
            session.set_value(ck, str(item_staged[ck]))
            applied = True
    skip_fields = item_staged.get("_skip")
    if skip_fields:
        if isinstance(skip_fields, str):
            skip_fields = [skip_fields]
        for sk in skip_fields:
            if (
                sk in child_keys
                and not session.has_field(sk)
                and not session.is_skipped(sk)
            ):
                session.skip_field(sk)
                applied = True
    if not stage_store:
        session.context.pop("_for_each_staged", None)
    return applied


def auto_advance_staged_for_each(
    session: InterviewSession, spec: InterviewSpec
) -> bool:
    advanced = False
    while True:
        active = get_active_for_each(session, spec)
        if not active:
            break
        child_keys = list(active.state.get("child_keys") or [])
        item = current_item(active.state)
        if item:
            apply_staged_fields(session, child_keys, item.get("id", ""))
        if not is_current_item_complete(session, child_keys):
            break
        complete_current_item(session, spec)
        advanced = True
    return advanced


def update_for_each_record_field(
    session: InterviewSession, spec: InterviewSpec, field_key: str, new_value: str
) -> bool:
    """Update a for_each child field in ALL completed records.

    Used during review corrections when a for_each iteration is already
    complete. Since child scratch fields are cleared after each item,
    corrections need to update the snapshotted record data directly.
    Returns True if any record was updated.
    """
    parent_key = find_child_field_parent(spec, field_key)
    if not parent_key:
        return False
    state = get_for_each_state(session, parent_key)
    if not state:
        return False
    records = state.get("records") or []
    if not isinstance(records, list):
        return False
    updated = False
    for rec in records:
        if not isinstance(rec, dict):
            continue
        fields_data = rec.get("fields")
        if not isinstance(fields_data, dict):
            continue
        if field_key in fields_data or field_key in (rec.get("skipped_fields") or []):
            fields_data[field_key] = new_value
            skipped = rec.get("skipped_fields")
            if isinstance(skipped, list) and field_key in skipped:
                skipped.remove(field_key)
            updated = True
    return updated


def apply_for_each_staged_to_records(
    session: InterviewSession, spec: InterviewSpec, staged: Dict[str, Dict[str, str]]
) -> bool:
    """Route for_each_staged data directly into completed records.

    Used when a for_each iteration is complete and the model submits
    per-item corrections via for_each_staged during review. Keys in
    *staged* are 1-based indices matching the for_each.index metadata.
    Returns True if any record was updated.
    """
    store = get_for_each_store(session)
    updated = False
    for idx_str, item_values in staged.items():
        try:
            idx = int(idx_str) - 1
        except (ValueError, TypeError):
            continue
        if idx < 0:
            continue
        for parent_key, state in store.items():
            if not isinstance(state, dict):
                continue
            records = state.get("records") or []
            if not isinstance(records, list):
                continue
            if idx >= len(records):
                continue
            rec = records[idx]
            if not isinstance(rec, dict):
                continue
            fields_data = rec.get("fields")
            if not isinstance(fields_data, dict):
                continue
            child_keys_set = set(state.get("child_keys") or [])
            for ck, cv in item_values.items():
                if ck in child_keys_set:
                    fields_data[ck] = str(cv)
                    skipped = rec.get("skipped_fields")
                    if isinstance(skipped, list) and ck in skipped:
                        skipped.remove(ck)
                    updated = True
    return updated


def wipe_parent_for_each(
    session: InterviewSession, spec: InterviewSpec, parent_key: str
) -> None:
    store = get_for_each_store(session)
    store.pop(parent_key, None)
    parent = spec.get_field(parent_key)
    if parent and parent.for_each:
        clear_child_scratch(session, child_keys_for_parent(parent))


def get_active_for_each(
    session: InterviewSession, spec: InterviewSpec
) -> Optional[ActiveForEach]:
    store = get_for_each_store(session)
    for parent_key, state in store.items():
        if not isinstance(state, dict):
            continue
        if state.get("status") != STATUS_ACTIVE:
            continue
        parent = spec.get_field(parent_key)
        if parent and parent.for_each:
            return ActiveForEach(parent_key, parent, state)
    return None


def get_for_each_state(
    session: InterviewSession, parent_key: str
) -> Optional[Dict[str, Any]]:
    state = get_for_each_store(session).get(parent_key)
    return state if isinstance(state, dict) else None


def is_for_each_child_key(
    session: InterviewSession, spec: InterviewSpec, key: str
) -> bool:
    active = get_active_for_each(session, spec)
    if not active:
        return False
    return key in set(active.state.get("child_keys") or [])


def resolve_field_def(
    session: InterviewSession, spec: InterviewSpec, key: str
) -> Optional[FieldDef]:
    top = spec.get_field(key)
    if top:
        return top
    parent_key = find_child_field_parent(spec, key)
    if parent_key:
        parent = spec.get_field(parent_key)
        if parent and parent.for_each:
            for child in parent.for_each.fields:
                if child.key == key:
                    return child
    return None


def find_child_field_parent(spec: InterviewSpec, child_key: str) -> Optional[str]:
    for f in spec.fields:
        if f.for_each:
            for child in f.for_each.fields:
                if child.key == child_key:
                    return f.key
    return None


def init_expansion(
    session: InterviewSession,
    parent_key: str,
    parent_fdef: FieldDef,
    items: List[Any],
    *,
    skip: bool = False,
) -> None:
    if not isinstance(session.context, dict):
        session.context = {}
    store = session.context.setdefault(FOR_EACH_CONTEXT_KEY, {})
    child_keys = child_keys_for_parent(parent_fdef)
    if skip or not items:
        store[parent_key] = {
            "status": STATUS_SKIPPED,
            "items": [],
            "records": [],
            "child_keys": child_keys,
        }
        return
    normalized: List[Dict[str, str]] = []
    for i, item in enumerate(items):
        if isinstance(item, dict):
            item_id = str(item.get("id") or item.get("item_id") or i)
            label = str(item.get("label") or item_id)
            value = str(item.get("value") or item_id)
        else:
            item_id = str(item)
            label = item_id
            value = item_id
        normalized.append({"id": item_id, "label": label, "value": value})

    old_state = store.get(parent_key)
    old_records_by_id: Dict[str, Any] = {}
    if isinstance(old_state, dict):
        for rec in old_state.get("records") or []:
            if isinstance(rec, dict) and rec.get("item_id"):
                old_records_by_id[rec["item_id"]] = rec

    preserved_records: List[Dict[str, Any]] = []
    first_incomplete_idx = len(normalized)
    for idx, item in enumerate(normalized):
        rec = old_records_by_id.get(item["id"])
        if rec is not None:
            preserved_records.append(rec)
        else:
            first_incomplete_idx = min(first_incomplete_idx, idx)

    all_complete = first_incomplete_idx >= len(normalized)
    store[parent_key] = {
        "status": STATUS_COMPLETE if all_complete else STATUS_ACTIVE,
        "items": normalized,
        "current_index": first_incomplete_idx if not all_complete else len(normalized),
        "child_keys": child_keys,
        "records": preserved_records,
    }

    if not all_complete and preserved_records:
        if not isinstance(session.context, dict):
            session.context = {}
        stage_store = session.context.setdefault("_for_each_staged", {})
        for rec in preserved_records:
            item_id = rec.get("item_id", "")
            if not item_id:
                continue
            fields_data = rec.get("fields")
            if not isinstance(fields_data, dict):
                continue
            item_staged = stage_store.setdefault(item_id, {})
            for ck in child_keys:
                if ck in fields_data and ck not in item_staged:
                    item_staged[ck] = str(fields_data[ck])
            skipped_fields = rec.get("skipped_fields") or []
            if isinstance(skipped_fields, list):
                skip_key = "_skip"
                existing_skip = item_staged.get(skip_key)
                if existing_skip and isinstance(existing_skip, list):
                    for sk in skipped_fields:
                        if sk not in existing_skip and sk in child_keys:
                            existing_skip.append(sk)
                else:
                    item_staged[skip_key] = list(skipped_fields)

    if isinstance(session.context, dict):
        stage_store = session.context.get("_for_each_staged")
        if isinstance(stage_store, dict):
            current_ids = {it["id"] for it in normalized}
            for orphan_id in list(stage_store.keys()):
                if orphan_id not in current_ids:
                    stage_store.pop(orphan_id)
            if not stage_store:
                session.context.pop("_for_each_staged", None)


def apply_for_each_expand(
    session: InterviewSession,
    spec: InterviewSpec,
    parent_key: str,
    expand_data: Dict[str, Any],
) -> None:
    parent = spec.get_field(parent_key)
    if not parent or not parent.for_each:
        return
    skip = bool(expand_data.get("skip"))
    items = expand_data.get("items") or []
    if not isinstance(items, list):
        items = []
    init_expansion(session, parent_key, parent, items, skip=skip)


def apply_default_expand_after_parent_store(
    session: InterviewSession, spec: InterviewSpec, parent_key: str
) -> None:
    """When a parent with for_each stores but post_processor omitted expand, skip."""
    parent = spec.get_field(parent_key)
    if not parent or not parent.for_each:
        return
    if get_for_each_state(session, parent_key) is not None:
        return
    init_expansion(session, parent_key, parent, [], skip=True)


def current_item(state: Dict[str, Any]) -> Optional[Dict[str, str]]:
    idx = int(state.get("current_index") or 0)
    items = state.get("items") or []
    if idx < len(items):
        item = items[idx]
        return item if isinstance(item, dict) else None
    return None


def is_current_item_complete(session: InterviewSession, child_keys: List[str]) -> bool:
    return all(session.has_field(ck) or session.is_skipped(ck) for ck in child_keys)


def complete_current_item(session: InterviewSession, spec: InterviewSpec) -> bool:
    """Snapshot the current item and advance or finish. Returns True when all done."""
    active = get_active_for_each(session, spec)
    if not active:
        return False
    state = active.state
    child_keys = list(state.get("child_keys") or [])
    item = current_item(state)

    fields_data: Dict[str, str] = {}
    skipped: List[str] = []
    for ck in child_keys:
        if session.is_skipped(ck):
            skipped.append(ck)
        elif session.has_field(ck):
            val = session.get_value(ck)
            if val is not None:
                fields_data[ck] = val

    if item:
        records = state.setdefault("records", [])
        if not isinstance(records, list):
            records = []
            state["records"] = records
        records.append(
            {
                "item_id": item.get("id", ""),
                "label": item.get("label", ""),
                "fields": fields_data,
                "skipped_fields": skipped,
            }
        )

    clear_child_scratch(session, child_keys)

    idx = int(state.get("current_index") or 0) + 1
    items = state.get("items") or []
    if idx >= len(items):
        state["status"] = STATUS_COMPLETE
        state["current_index"] = idx
        return True

    state["current_index"] = idx
    new_item = items[idx] if idx < len(items) else None
    new_item_id = new_item.get("id", "") if isinstance(new_item, dict) else ""
    if new_item_id:
        apply_staged_fields(session, child_keys, new_item_id)
    return False


def maybe_advance_for_each(
    session: InterviewSession, spec: InterviewSpec, field_key: str
) -> bool:
    """After a child store or skip, advance iteration if the current item is done."""
    active = get_active_for_each(session, spec)
    if not active:
        return False
    child_keys = list(active.state.get("child_keys") or [])
    if field_key not in child_keys:
        return False
    if not is_current_item_complete(session, child_keys):
        return False
    return complete_current_item(session, spec)


def next_unanswered_child_key(
    session: InterviewSession, spec: InterviewSpec
) -> Optional[str]:
    active = get_active_for_each(session, spec)
    if not active:
        return None
    child_keys = list(active.state.get("child_keys") or [])
    for ck in child_keys:
        if not session.has_field(ck) and not session.is_skipped(ck):
            return ck
    return None


def is_parent_for_each_blocking(
    session: InterviewSession, spec: InterviewSpec, parent_key: str
) -> bool:
    """True when parent is stored but for_each expansion is pending or active."""
    parent = spec.get_field(parent_key)
    if not parent or not parent.for_each:
        return False
    if not session.has_field(parent_key):
        return False
    state = get_for_each_state(session, parent_key)
    if state is None:
        return True
    return state.get("status") == STATUS_ACTIVE


def reachable_for_each_child_keys(
    session: InterviewSession, spec: InterviewSpec
) -> List[str]:
    """Child scratch keys that belong on the active path during expansion."""
    active = get_active_for_each(session, spec)
    if not active:
        return []
    return list(active.state.get("child_keys") or [])


def field_sort_order(
    session: InterviewSession, spec: InterviewSpec, key: str
) -> Tuple[int, int]:
    top_keys = spec.field_keys()
    if key in top_keys:
        return top_keys.index(key), 0
    active = get_active_for_each(session, spec)
    if active and key in set(active.state.get("child_keys") or []):
        parent_idx = next(
            (i for i, k in enumerate(top_keys) if k == active.parent_key),
            len(top_keys),
        )
        child_idx = list(active.state.get("child_keys") or []).index(key)
        item_idx = int(active.state.get("current_index") or 0)
        return parent_idx, 100 + item_idx * 10 + child_idx
    parent = find_child_field_parent(spec, key)
    if parent and parent in top_keys:
        return top_keys.index(parent), 999
    return len(top_keys), 999


def build_for_each_metadata(
    session: InterviewSession, spec: InterviewSpec, parent_key: str
) -> Optional[Dict[str, Any]]:
    state = get_for_each_state(session, parent_key)
    if not state or state.get("status") != STATUS_ACTIVE:
        return None
    item = current_item(state)
    if not item:
        return None
    idx = int(state.get("current_index") or 0) + 1
    items = state.get("items") or []
    return {
        "parent": parent_key,
        "index": idx,
        "total": len(items),
        "label": item.get("label", ""),
        "value": item.get("value", item.get("id", "")),
    }


def _default_for_each_prefix(
    index: int, total: int, field_key: str, field_value: str
) -> str:
    if total == 1:
        return ""
    singular = _singularize_key(field_key)
    return (
        f"For the {_ordinal(index)} {singular} {field_value} "
        "(state this prefix only when clarity needs it; "
        "do not repeat it for every subpart question of the same item):"
    )


def build_prefixed_child_prompt(
    parent_fdef: FieldDef,
    child_fdef: FieldDef,
    state: Dict[str, Any],
    *,
    prefix_fn: Optional[Callable] = None,
) -> str:
    item = current_item(state)
    idx = int(state.get("current_index") or 0) + 1
    items = state.get("items") or []
    total = len(items)
    label = item.get("label", "") if item else ""
    field_value = item.get("value", item.get("id", "")) if item else ""

    if prefix_fn is not None:
        try:
            prefix = prefix_fn(idx, total, label, parent_fdef.key, field_value).strip()
        except Exception:
            logger.warning(
                "for_each_prefix function failed for field %s, using default",
                parent_fdef.key,
                exc_info=True,
            )
            prefix = _default_for_each_prefix(idx, total, parent_fdef.key, field_value)
    else:
        prefix = _default_for_each_prefix(idx, total, parent_fdef.key, field_value)

    prompt = child_fdef.prompt.strip()
    if prefix:
        return f"{prefix} {prompt}".strip()
    return prompt


def for_each_review_sections(
    session: InterviewSession,
    spec: InterviewSpec,
) -> List[str]:
    """Markdown record blocks for completed for_each records on the review summary.

    Always renders the per-item section for every parent with ``for_each``.
    A custom review handler that hides the parent's own raw-value line does so
    via ``modified_values`` → ``__omit__`` (consumed by ``build_review_summary``'s
    top-level loop); that suppression must NOT also drop the per-item records,
    which are the only place those collected child values are surfaced.

    Returns one string per record — the header and its child lines joined by a
    single newline so a record renders as a tight block. The caller joins these
    blocks with a blank-line separator (``\\n\\n``), producing one blank line
    between records and no blank lines within a record.
    """
    store = get_for_each_store(session)
    blocks: List[str] = []
    for fdef in spec.fields:
        if not fdef.for_each:
            continue
        state = store.get(fdef.key)
        if not isinstance(state, dict):
            continue
        records = state.get("records") or []
        if not isinstance(records, list) or not records:
            continue
        for rec in records:
            if not isinstance(rec, dict):
                continue
            label = rec.get("label") or rec.get("item_id") or "Item"
            record_lines: List[str] = [
                f"**{_singularize_key(fdef.key).title()} — {label}**"
            ]
            fields_data = rec.get("fields") or {}
            skipped = set(rec.get("skipped_fields") or [])
            if isinstance(fields_data, dict):
                for child in fdef.for_each.fields:
                    if child.key in skipped:
                        record_lines.append(
                            f"  **{child.key.replace('_', ' ').title()}**: (skipped)"
                        )
                    elif child.key in fields_data:
                        record_lines.append(
                            f"  **{child.key.replace('_', ' ').title()}**: "
                            f"{fields_data[child.key]}"
                        )
            blocks.append("\n".join(record_lines))
    return blocks
