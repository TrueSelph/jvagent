"""Native interview domain tools; official Harness owns their execution."""

import json
import os
from dataclasses import fields
from pathlib import Path
from typing import Any

from jvagent.harness.conversation_tools import ConversationMutationTool
from jvagent.tooling.tool import Tool

from .flow import evaluate_operator
from .hooks import interview_tool_response
from .session import load_session
from .spec import InterviewSpec
from .tools import _build_core_tools
from .validators import BUILTIN_VALIDATORS


class InterviewMutationTool(ConversationMutationTool):
    """Bind shared domain tools to their configured, loaded owning skill."""

    @property
    def skill_name_argument(self) -> str | None:
        return "interview_type" if self.name == "interview__start" else None

    def required_skill(self, arguments: dict[str, Any]) -> str | None:
        from jvagent.tooling.context import require_tool_visitor

        if self.name == "interview__start":
            return arguments.get("interview_type")
        session = load_session(require_tool_visitor().conversation)
        return session.interview_type if session is not None else None


def qualify_spec(
    spec: InterviewSpec,
    *,
    channel: str | None = None,
    tool_name: str | None = None,
    hook_digest: str | None = None,
) -> None:
    """Admit reviewed domain tools and host-approved local hook sources."""
    if spec.source_dir:
        from jvagent.harness.skill_assets import project_skill

        directory = Path(spec.source_dir).resolve(strict=True)
        metadata, _ = project_skill(directory, action_bases=native_skill_bases())
        if metadata["name"] != spec.name or directory.name != spec.name:
            raise ValueError("Native interview names must match the authored skill")
        if metadata.get("output-contract"):
            raise ValueError("Native interview output contracts require qualification")
        allowed_channels = metadata.get("allowed-channels", [])
        if channel is not None and (
            channel in metadata.get("denied-channels", [])
            or (allowed_channels and channel not in allowed_channels)
        ):
            raise ValueError("Interview skill is unavailable on this channel")
        if tool_name is not None and tool_name not in metadata.get("allowed-tools", []):
            raise ValueError("Interview skill does not permit this domain tool")
    if (
        spec.skill_tools
        or spec.parameters
        or any((spec.nudge_after, spec.abandon_after, spec.parked_expire_after))
    ):
        raise ValueError("Native interview hooks/tasks require separate qualification")
    extensions = bool(
        any(getattr(spec.handlers, item.name) for item in fields(spec.handlers))
        or any(
            f.pre_processor
            or f.post_processor
            or (f.validator and f.validator not in BUILTIN_VALIDATORS)
            for f in spec.fields
        )
    )
    if extensions:
        from .native_hooks import approved_source

        approved_source(spec, hook_digest)
    child_names: set[str] = set()
    for field in spec.fields:
        if field.for_each_prefix:
            raise ValueError("Native interview field extensions require qualification")
        if field.for_each:
            if (
                field.validator not in {"", "text"}
                or not field.for_each.fields
                or field.pre_processor
                or field.post_processor
            ):
                raise ValueError(
                    "Native repeated parents require text and child declarations"
                )
            for child in field.for_each.fields:
                if (
                    not child.key
                    or not child.key.strip()
                    or child.key == "_skip"
                    or child.key in child_names
                    or child.key in spec.field_keys()
                    or child.pre_processor
                    or child.post_processor
                    or child.for_each
                    or child.for_each_prefix
                    or child.branches
                    or child.else_field
                    or (child.validator and child.validator not in BUILTIN_VALIDATORS)
                ):
                    raise ValueError(
                        "Native repeated child declarations require unique keys and built-in validators"
                    )
                child_names.add(child.key)
    qualify_branches(spec)


def qualify_branches(spec: InterviewSpec) -> None:
    """Validate declarations; the existing domain resolver still walks the path."""
    keys = spec.field_keys()
    if any(not isinstance(key, str) or not key.strip() for key in keys) or len(
        keys
    ) != len(set(keys)):
        raise ValueError("Native interview field names must be unique and nonempty")
    edges: dict[str, set[str]] = {key: set() for key in keys}
    for index, field in enumerate(spec.fields):
        targets = []
        for branch in field.branches:
            condition = branch.when
            if (
                not isinstance(condition, dict)
                or not condition
                or set(condition) - {"op", "value"}
            ):
                raise ValueError(
                    "Native branch conditions require declarative operators; hooks are unqualified"
                )
            operator = condition.get("op", "equals")
            if not isinstance(operator, str):
                raise ValueError("Native branch operator must be a name")
            try:
                json.dumps(condition, allow_nan=False)
                evaluate_operator(operator, None, condition.get("value"))
            except (TypeError, ValueError) as error:
                raise ValueError("Native branch operator/value is invalid") from error
            if operator.strip().lower() in {
                "in",
                "in_list",
                "not_in",
                "not_in_list",
            } and not isinstance(condition.get("value"), list):
                raise ValueError("Membership branch operators require a list")
            targets.append(branch.goto)
        if field.else_field:
            targets.append(field.else_field)
        elif not field.branches and index + 1 < len(keys):
            targets.append(keys[index + 1])
        for target in targets:
            if not isinstance(target, str) or target not in edges:
                raise ValueError("Native branch target must name an existing field")
            edges[field.key].add(target)
    incoming = dict.fromkeys(keys, 0)
    for successors in edges.values():
        for target in successors:
            incoming[target] += 1
    ready = [key for key, count in incoming.items() if count == 0]
    visited = 0
    while ready:
        key = ready.pop()
        visited += 1
        for target in edges[key]:
            incoming[target] -= 1
            if incoming[target] == 0:
                ready.append(target)
    if visited != len(keys):
        raise ValueError(
            "Native interview branch cycles require a qualified repeat adapter"
        )


def native_skill_bases() -> dict[str, Path]:
    return {"jvagent/interview": Path(__file__).parent / "native_skill"}


def build_native_tools(action: Any) -> list[Tool]:
    """Expose explicit activation and reviewed domain tools without use_skill."""
    names = []
    for name, spec in action._registry.specs.items():
        try:
            qualify_spec(spec, hook_digest=action.native_hook_digests.get(name))
        except ValueError:
            continue
        names.append(name)
    if not names:
        return []

    async def start(interview_type: str, visitor: Any) -> str:
        spec = action._registry.get(interview_type)
        if spec is None or interview_type not in names:
            raise ValueError("Interview type is not qualified for native execution")
        qualify_spec(
            spec,
            channel=visitor.channel,
            tool_name="interview__start",
            hook_digest=action.native_hook_digests.get(spec.name),
        )
        return await action._handle_start(interview_type, visitor)

    activation = Tool(
        name="interview__start",
        description="Activate the selected interview and return its field catalog.",
        parameters_schema={
            "type": "object",
            "properties": {"interview_type": {"type": "string", "enum": sorted(names)}},
            "required": ["interview_type"],
            "additionalProperties": False,
        },
        execute=start,
    )

    descriptions = {
        "interview__start": "Activate the named interview and return its field catalog.",
        "interview__set_fields": "Validate and store supplied values for an active interview.",
        "interview__next_field": "Return the next unresolved interview field.",
        "interview__skip_field": "Skip a declined optional interview field.",
        "interview__field_unavailable": "Apply the declared unavailable-field policy.",
        "interview__get_status": "Return interview progress and the current field catalog.",
        "interview__review": "Review collected values and request the declared confirmation.",
        "interview__complete": "Complete after review and the required user confirmation.",
        "interview__cancel": "Cancel the current interview and clear its scratch state.",
        "interview__reset": "Reset the current interview through its domain lifecycle.",
        "interview__set_items": "Declare or replace the ordered repeated item IDs and labels.",
        "interview__set_item_fields": "Validate answers or optional skips for exactly one declared item ID.",
    }

    def wrap(original: Tool) -> Tool:
        async def invoke(visitor: Any, **arguments: Any) -> Any:
            # Existing TaskStore callbacks are external effects. Refuse configured
            # callbacks rather than silently bypassing them or claiming graph-only work.
            if any(
                os.environ.get(f"JVAGENT_TASK_{event}_WEBHOOK_URL")
                for event in ("CREATED", "UPDATED", "COMPLETED", "FAILED", "CANCELLED")
            ):
                raise ValueError(
                    "Native interview task callbacks require external-effect qualification"
                )
            scheduler = await visitor._agent.get_action_by_type(
                "TaskCreationInteractAction"
            )
            if scheduler is not None and getattr(
                scheduler, "task_created_webhook_url", None
            ):
                raise ValueError(
                    "Native interview task callbacks require external-effect qualification"
                )
            existing = load_session(visitor.conversation)
            if existing is not None:
                spec = action._registry.get(existing.interview_type)
                if spec is None:
                    raise ValueError("Active interview has no admitted contract")
                qualify_spec(
                    spec,
                    channel=visitor.channel,
                    tool_name=original.name,
                    hook_digest=action.native_hook_digests.get(spec.name),
                )
                if original.name == "interview__set_fields":
                    repeated_keys = spec.all_for_each_child_keys() | {
                        f.key for f in spec.fields if f.for_each
                    }
                    if arguments.get("for_each_staged") or repeated_keys.intersection(
                        arguments.get("fields") or {}
                    ):
                        return interview_tool_response(
                            ok=False,
                            status="active",
                            error="Use interview__set_items for item IDs and interview__set_item_fields for item answers; native repeated values require an item ID.",
                        )
                if original.name == "interview__skip_field":
                    from .for_each import get_active_for_each

                    key = arguments.get("field_key") or arguments.get("field")
                    if key in spec.all_for_each_child_keys() or (
                        not key and get_active_for_each(existing, spec)
                    ):
                        return interview_tool_response(
                            ok=False,
                            status="active",
                            error="Use interview__set_item_fields with the item ID and skip_fields for repeated optional fields.",
                        )
            owner_spec = (
                action._registry.get(existing.interview_type)
                if existing
                else action._registry.get(arguments.get("interview_type"))
            )
            if owner_spec is None:
                result = await original.call(visitor=visitor, **arguments)
            else:
                from .native_hooks import bind_native_hooks

                with bind_native_hooks(
                    owner_spec, action.native_hook_digests.get(owner_spec.name)
                ):
                    result = await original.call(visitor=visitor, **arguments)
            payload = json.loads(result.content)
            owner = (
                existing.interview_type if existing else arguments.get("interview_type")
            )
            if owner:
                handles = visitor.tasks.list(owner_action=owner)
                if payload.get("status") in {"completed", "cancelled"} and any(
                    handle.status == "active" for handle in handles
                ):
                    raise RuntimeError(
                        "Native interview task did not reach its terminal state"
                    )
                for handle in handles:
                    if handle.data.get("interview_managed") and "state" in handle.data:
                        # TaskStore.status is canonical. Retire the old engine's
                        # duplicate data.state field for tasks touched by native tools.
                        handle.data.pop("state")
                        await handle.update()
            return result

        values = {item.name: getattr(original, item.name) for item in fields(Tool)}
        values.update(
            execute=invoke,
            binds_visitor=True,
            effect_class="write",
            description=descriptions[original.name],
        )
        return InterviewMutationTool(**values)

    extra = []
    if any(
        field.for_each for name in names for field in action._registry.get(name).fields
    ):
        from .native_repeats import repeated_tools

        extra = repeated_tools(action)
    return [
        wrap(activation),
        *(wrap(tool) for tool in [*_build_core_tools(action), *extra]),
    ]
