"""Declarative JV schemas through the SDK's public argument-validator hook."""

from collections.abc import Collection, Mapping, Sequence
from copy import deepcopy
from dataclasses import replace
from typing import Any, Callable

from jsonschema import Draft202012Validator, ValidationError
from jsonschema.validators import validator_for
from pydantic_ai import ModelRetry
from referencing import Registry
from referencing.exceptions import Unresolvable

from jvagent.harness.contracts import reject_model_authority_fields


def argument_validator(
    schema: dict[str, Any],
    *,
    required_skill: Callable[[dict[str, Any]], str | None] | None = None,
    configured_skills: Collection[str] = (),
) -> Callable[..., None]:
    """No coercion, remote schema retrieval, argument values in errors or I/O."""
    validator_type = (
        validator_for(schema, default=None)
        if "$schema" in schema
        else Draft202012Validator
    )
    if validator_type is None:
        raise ValueError("Unsupported Action tool schema dialect")
    try:
        validator_type.check_schema(schema)
        validator = validator_type(schema, registry=Registry())
    except Exception:
        raise ValueError("Invalid Action tool schema") from None

    def validate(ctx: Any, /, **arguments: Any) -> None:
        reject_model_authority_fields(arguments)
        try:
            validator.validate(arguments)
        except (ValidationError, Unresolvable, RecursionError):
            raise ModelRetry(
                "Tool arguments do not match the declared schema"
            ) from None
        if required_skill is not None:
            owner = required_skill(arguments)
            if owner is not None and (
                owner not in configured_skills or owner not in ctx.active_capability_ids
            ):
                raise ModelRetry(
                    "Load the configured owning skill before using this tool"
                )

    return validate


def scope_skill_schema(
    schema: dict[str, Any], argument: str, owners: Collection[str]
) -> None:
    """Narrow an owned schema copy to admitted domain owners before model work."""
    scoped = schema.get("properties", {}).get(argument, {})
    declared = scoped.get("enum", [])
    if (
        not isinstance(declared, list)
        or not declared
        or any(not isinstance(name, str) or not name for name in declared)
        or set(owners) - set(declared)
    ):
        raise ValueError("Configured skill has no admitted domain definition")
    scoped["enum"] = [name for name in declared if name in owners]


def prepare_skill_tools(
    definitions: Sequence[Any],
    mutations: Mapping[str, Any],
    owners: Mapping[str, set[str]],
    active: Collection[str],
) -> list[Any]:
    """Project SDK definitions; shared tools cannot advertise other workflows."""
    prepared = []
    for definition in definitions:
        original = mutations.get(definition.name)
        argument = original.skill_name_argument if original is not None else None
        if argument:
            schema = deepcopy(definition.parameters_json_schema)
            scope_skill_schema(
                schema, argument, owners.get(definition.name, set()) & set(active)
            )
            if not schema["properties"][argument]["enum"]:
                continue
            definition = replace(definition, parameters_json_schema=schema)
        prepared.append(definition)
    return prepared
