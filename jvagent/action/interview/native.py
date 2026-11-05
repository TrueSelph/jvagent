"""Native interview domain tools; official Harness owns their execution."""

import json
import os
from dataclasses import fields
from pathlib import Path
from typing import Any

from jvagent.harness.conversation_tools import ConversationMutationTool
from jvagent.tooling.tool import Tool

from .session import load_session
from .spec import InterviewSpec
from .tools import _build_core_tools
from .validators import BUILTIN_VALIDATORS


def qualify_spec(
    spec: InterviewSpec, *, channel: str | None = None, tool_name: str | None = None
) -> None:
    """Admit the reviewed built-in domain surface, never arbitrary hook effects."""
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
        or any(getattr(spec.handlers, item.name) for item in fields(spec.handlers))
        or any((spec.nudge_after, spec.abandon_after, spec.parked_expire_after))
    ):
        raise ValueError("Native interview hooks/tasks require separate qualification")
    for field in spec.fields:
        if (
            field.pre_processor
            or field.post_processor
            or field.for_each
            or field.branches
            or field.else_field
            or (field.validator and field.validator not in BUILTIN_VALIDATORS)
        ):
            raise ValueError("Native interview field extensions require qualification")


def native_skill_bases() -> dict[str, Path]:
    return {"jvagent/interview": Path(__file__).parent / "native_skill"}


def build_native_tools(action: Any) -> list[Tool]:
    """Expose explicit activation and reviewed domain tools without use_skill."""
    names = []
    for name, spec in action._registry.specs.items():
        try:
            qualify_spec(spec)
        except ValueError:
            continue
        names.append(name)
    if not names:
        return []

    async def start(interview_type: str, visitor: Any) -> str:
        spec = action._registry.get(interview_type)
        if spec is None or interview_type not in names:
            raise ValueError("Interview type is not qualified for native execution")
        qualify_spec(spec, channel=visitor.channel, tool_name="interview__start")
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
                qualify_spec(spec, channel=visitor.channel, tool_name=original.name)
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
        return ConversationMutationTool(**values)

    return [wrap(activation), *(wrap(tool) for tool in _build_core_tools(action))]
