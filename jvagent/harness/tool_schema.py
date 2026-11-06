"""Declarative JV schemas through the SDK's public argument-validator hook."""

from typing import Any, Callable

from jsonschema import Draft202012Validator, ValidationError
from jsonschema.validators import validator_for
from pydantic_ai import ModelRetry
from referencing import Registry
from referencing.exceptions import Unresolvable

from jvagent.harness.contracts import reject_model_authority_fields


def argument_validator(schema: dict[str, Any]) -> Callable[..., None]:
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

    return validate
