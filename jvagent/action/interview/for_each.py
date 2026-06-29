"""for_each expansion — per-item subpart field iteration state and helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from .session import InterviewSession
from .spec import FieldDef, InterviewSpec

FOR_EACH_CONTEXT_KEY = "for_each"

STATUS_ACTIVE = "active"
STATUS_COMPLETE = "complete"
STATUS_SKIPPED = "skipped"

DEFAULT_PROMPT_PREFIX = "For item #{index}:"


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
    active = get_active_for_each(session, spec)
    if active:
        for child in active.parent_fdef.for_each.fields:  # type: ignore[union-attr]
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


def prompt_prefix_substitute(template: str, index: int, label: str) -> str:
    """Substitute ``#{index}``, ``{index}``, and ``{label}`` in a prompt prefix."""
    out = template.replace("{label}", label)
    out = out.replace("#{index}", str(index))
    out = out.replace("{index}", str(index))
    return out


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
        else:
            item_id = str(item)
            label = item_id
        normalized.append({"id": item_id, "label": label})
    store[parent_key] = {
        "status": STATUS_ACTIVE,
        "items": normalized,
        "current_index": 0,
        "child_keys": child_keys,
        "records": [],
    }


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
    }


def build_prefixed_child_prompt(
    parent_fdef: FieldDef, child_fdef: FieldDef, state: Dict[str, Any]
) -> str:
    prefix_tpl = (
        parent_fdef.for_each.prompt_prefix  # type: ignore[union-attr]
        if parent_fdef.for_each
        else DEFAULT_PROMPT_PREFIX
    )
    item = current_item(state)
    idx = int(state.get("current_index") or 0) + 1
    label = item.get("label", "") if item else ""
    prefix = prompt_prefix_substitute(prefix_tpl, idx, label).strip()
    prompt = child_fdef.prompt.strip()
    if prefix:
        return f"{prefix} {prompt}".strip()
    return prompt


def for_each_review_sections(
    session: InterviewSession,
    spec: InterviewSpec,
    *,
    omit_parents: Optional[set] = None,
) -> List[str]:
    """Markdown lines for completed for_each records on the review summary.

    ``omit_parents`` mirrors the ``omit_fields`` set in ``build_review_summary``
    — when a custom review handler hides a parent field from the summary, its
    per-item records are suppressed here too.
    """
    store = get_for_each_store(session)
    lines: List[str] = []
    for fdef in spec.fields:
        if not fdef.for_each:
            continue
        if omit_parents and fdef.key in omit_parents:
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
            lines.append(f"**{fdef.key.replace('_', ' ').title()} — {label}**")
            fields_data = rec.get("fields") or {}
            skipped = set(rec.get("skipped_fields") or [])
            if isinstance(fields_data, dict):
                for child in fdef.for_each.fields:
                    if child.key in skipped:
                        lines.append(
                            f"  **{child.key.replace('_', ' ').title()}**: (skipped)"
                        )
                    elif child.key in fields_data:
                        lines.append(
                            f"  **{child.key.replace('_', ' ').title()}**: "
                            f"{fields_data[child.key]}"
                        )
    return lines
