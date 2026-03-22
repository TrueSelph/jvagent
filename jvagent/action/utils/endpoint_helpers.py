"""Shared endpoint helpers for resolving typed actions."""

from typing import Type, TypeVar

from jvspatial.api.exceptions import ResourceNotFoundError, ValidationError

from jvagent.action.base import Action

T = TypeVar("T", bound=Action)


async def require_typed_action(
    action_id: str,
    action_cls: Type[T],
    *,
    not_found_message: str,
    wrong_type_message: str,
) -> T:
    """Load an action by ID and ensure it is an instance of ``action_cls``."""
    action = await action_cls.get(action_id)
    if not action:
        raise ResourceNotFoundError(
            message=not_found_message,
            details={"action_id": action_id},
        )
    if not isinstance(action, action_cls):
        raise ValidationError(
            message=wrong_type_message,
            details={"action_id": action_id},
        )
    return action
