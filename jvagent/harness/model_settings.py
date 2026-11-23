"""Validate persisted settings against the public native SDK contracts."""

from functools import lru_cache
from typing import Any


def validate_tool_execution_mode(value: Any) -> Any:
    """Use the SDK's public mode type; never print untrusted setting values."""
    from pydantic import TypeAdapter, ValidationError
    from pydantic_ai.agent import ParallelExecutionMode

    try:
        return TypeAdapter(ParallelExecutionMode).validate_python(value, strict=True)
    except ValidationError:
        raise ValueError("Invalid native tool execution mode") from None


@lru_cache(maxsize=1)
def _settings_adapter() -> Any:
    from pydantic import ConfigDict, TypeAdapter, with_config
    from pydantic_ai.settings import ModelSettings

    @with_config(
        ConfigDict(
            extra="forbid",
            strict=True,
            arbitrary_types_allowed=True,
            allow_inf_nan=False,
        )
    )
    class DeclaredSettings(ModelSettings):
        pass

    return TypeAdapter(DeclaredSettings)


def validate_model_settings(value: dict[str, Any] | None) -> dict[str, Any]:
    """Reject misspelled keys/types without copying the SDK's field definitions."""
    from pydantic import ValidationError

    try:
        return dict(_settings_adapter().validate_python({} if value is None else value))
    except ValidationError as exc:
        details = exc.errors(
            include_input=False, include_context=False, include_url=False
        )
        raise ValueError(f"Invalid native model settings: {details}") from None
