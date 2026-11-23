"""Explicit repeated-item domain mutations, dispatched only by the official SDK."""

from copy import deepcopy
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from jvagent.tooling.tool import Tool

from .flow import (
    compute_active_path_for_prune,
    prune_orphan_for_each_states,
    prune_unreachable_fields,
)
from .for_each import (
    apply_staged_fields,
    auto_advance_staged_for_each,
    child_keys_for_parent,
    current_item,
    get_active_for_each,
    get_for_each_state,
    init_expansion,
    maybe_advance_for_each,
)
from .hooks import interview_tool_response, run_validator
from .session import InterviewStatus


class Item(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)
    id: str = Field(min_length=1, max_length=128)
    label: str | None = Field(default=None, min_length=1, max_length=256)


class Items(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)
    parent_key: str
    items: list[Item] = Field(min_length=1, max_length=100)


class ItemFields(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)
    parent_key: str
    item_id: str
    fields: dict[
        str,
        Annotated[str, Field(max_length=4096)]
        | int
        | Annotated[float, Field(allow_inf_nan=False)]
        | bool,
    ] = Field(default_factory=dict, max_length=100)
    skip_fields: list[str] = Field(default_factory=list, max_length=100)


def repeated_tools(action: Any) -> list[Tool]:
    async def set_items(visitor: Any, **arguments: Any) -> str:
        return await mutate_items(action, visitor, arguments, replace=True)

    async def set_fields(visitor: Any, **arguments: Any) -> str:
        return await mutate_items(action, visitor, arguments, replace=False)

    return [
        Tool(
            name="interview__set_items",
            description="Declare or replace an ordered item collection by unique item IDs; retain matching completed answers.",
            parameters_schema=Items.model_json_schema(),
            execute=set_items,
        ),
        Tool(
            name="interview__set_item_fields",
            description="Validate answers or optional skips for exactly one repeated item ID, including future items and corrections.",
            parameters_schema=ItemFields.model_json_schema(),
            execute=set_fields,
        ),
    ]


async def mutate_items(
    action: Any, visitor: Any, arguments: dict[str, Any], *, replace: bool
) -> str:
    """Validate a full patch on a copy, then persist one domain-session change."""
    try:
        request: Items | ItemFields
        if replace:
            request = Items.model_validate(arguments)
        else:
            request = ItemFields.model_validate(arguments)
    except ValidationError:
        return interview_tool_response(
            ok=False, status="error", error="Invalid repeated-item arguments."
        )
    original, spec = await action._get_session_and_contract(visitor)
    if original is None or spec is None or not original.is_active():
        return interview_tool_response(
            ok=False, status="error", error="Start an active interview first."
        )
    parent = spec.get_field(request.parent_key)
    if parent is None or parent.for_each is None:
        return interview_tool_response(
            ok=False, status="error", error="Unknown repeated parent field."
        )
    session = deepcopy(original)
    reachable = await compute_active_path_for_prune(
        session, spec, action._load_fn(spec), visitor, action
    )
    if parent.key not in reachable:
        return interview_tool_response(
            ok=False,
            status="active",
            error="The repeated parent is outside the active path.",
        )
    active = get_active_for_each(session, spec)
    state = get_for_each_state(session, parent.key)
    if replace:
        assert isinstance(request, Items)
        if active and active.parent_key != parent.key:
            return interview_tool_response(
                ok=False,
                status="active",
                error="Finish the active item collection before replacing another.",
            )
        ids = [item.id for item in request.items]
        if len(set(ids)) != len(ids):
            return interview_tool_response(
                ok=False, status="error", error="Item IDs must be unique."
            )
        checked = await run_validator(
            action, spec, parent, ", ".join(ids), session, visitor
        )
        if not checked.get("valid"):
            return interview_tool_response(
                ok=False, status="active", error=checked.get("error")
            )
        # Pending scratch belongs to its item, never to the item replacing it.
        if active:
            item = current_item(active.state)
            if item:
                pending = session.context.setdefault("_for_each_staged", {}).setdefault(
                    item["id"], {}
                )
                pending.update(
                    {
                        key: session.get_value(key)
                        for key in child_keys_for_parent(parent)
                        if session.has_field(key)
                    }
                )
                pending["_skip"] = [
                    key
                    for key in child_keys_for_parent(parent)
                    if session.is_skipped(key)
                ]
        from .for_each import clear_child_scratch

        clear_child_scratch(session, child_keys_for_parent(parent))
        previous_labels = {
            item["id"]: item["label"] for item in (state or {}).get("items", [])
        }
        items = [
            {
                "id": item.id,
                "label": item.label or previous_labels.get(item.id, item.id),
            }
            for item in request.items
        ]
        session.set_value(parent.key, checked.get("value", ", ".join(ids)))
        init_expansion(session, parent.key, parent, items)
        state = get_for_each_state(session, parent.key)
        if state and state["status"] == "active":
            apply_staged_fields(
                session,
                child_keys_for_parent(parent),
                state["items"][state["current_index"]]["id"],
            )
    else:
        assert isinstance(request, ItemFields)
        if state is None or state.get("status") == "skipped":
            return interview_tool_response(
                ok=False,
                status="active",
                error="Declare the item collection with interview__set_items first.",
            )
        indices = {item["id"]: index for index, item in enumerate(state["items"])}
        children = {child.key: child for child in parent.for_each.fields}
        if (
            request.item_id not in indices
            or not (request.fields or request.skip_fields)
            or (set(request.fields) | set(request.skip_fields)) - children.keys()
            or set(request.fields) & set(request.skip_fields)
            or any(children[key].required for key in request.skip_fields)
        ):
            return interview_tool_response(
                ok=False,
                status="error",
                error="Supply declared fields or optional skips for an existing item ID.",
            )
        cleaned: dict[str, str] = {}
        for key, value in request.fields.items():
            value = str(value)
            checked = await run_validator(
                action, spec, children[key], value, session, visitor
            )
            if not value or not checked.get("valid"):
                return interview_tool_response(
                    ok=False,
                    status="active",
                    error=checked.get("error") or "Empty field value.",
                    field=key,
                )
            cleaned[key] = str(checked.get("value", value))
        record = next(
            (rec for rec in state["records"] if rec["item_id"] == request.item_id), None
        )
        if record is not None:
            record["fields"].update(cleaned)
            for key in request.skip_fields:
                record["fields"].pop(key, None)
            record["skipped_fields"] = sorted(
                (set(record.get("skipped_fields", [])) | set(request.skip_fields))
                - cleaned.keys()
            )
        elif (
            active
            and active.parent_key == parent.key
            and current_item(state)["id"] == request.item_id
        ):
            for key, value in cleaned.items():
                session.set_value(key, value)
            for key in request.skip_fields:
                session.skip_field(key)
            maybe_advance_for_each(
                session, spec, next(iter(cleaned or dict.fromkeys(request.skip_fields)))
            )
        elif active and active.parent_key == parent.key:
            pending = session.context.setdefault("_for_each_staged", {}).setdefault(
                request.item_id, {}
            )
            pending.update(cleaned)
            for key in request.skip_fields:
                pending.pop(key, None)
            pending["_skip"] = sorted(
                (set(pending.get("_skip", [])) | set(request.skip_fields))
                - cleaned.keys()
            )
        else:
            return interview_tool_response(
                ok=False,
                status="error",
                error="The requested item is not active or complete.",
            )
    auto_advance_staged_for_each(session, spec)
    reachable = await compute_active_path_for_prune(
        session, spec, action._load_fn(spec), visitor, action
    )
    prune_unreachable_fields(session, reachable)
    prune_orphan_for_each_states(session, spec, reachable)
    if get_active_for_each(session, spec):
        session.status = InterviewStatus.ACTIVE
    await action._save_session(session, visitor)
    if replace:
        return await action._handle_get_status(visitor)
    from .flow import build_next_field

    assert isinstance(request, ItemFields)
    state = get_for_each_state(session, parent.key)
    record = next(
        (r for r in state["records"] if r["item_id"] == request.item_id), None
    )
    active_item = current_item(state) if state["status"] == "active" else None
    if record is None:
        values = (
            session.fields
            if active_item and active_item["id"] == request.item_id
            else session.context.get("_for_each_staged", {}).get(request.item_id, {})
        )
        record = {
            "item_id": request.item_id,
            "fields": {key: values[key] for key in children if key in values},
            "skipped_fields": sorted(
                session.skipped_fields.intersection(children)
                if active_item and active_item["id"] == request.item_id
                else values.get("_skip", [])
            ),
        }
    next_field = await build_next_field(
        session, spec, action._load_fn(spec), visitor, action
    )
    return interview_tool_response(
        ok=True,
        status=session.status.value,
        parent_key=parent.key,
        updated_item=record,
        current_item=active_item,
        collection_status=state["status"],
        next_field_key=next_field["key"] if next_field else None,
    )
