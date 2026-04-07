"""Endpoint helpers for EmailAction (WhatsApp parity)."""

from jvspatial.api.exceptions import ResourceNotFoundError

from ..email_action import EmailAction


async def get_email_action(action_id: str) -> EmailAction:
    """Load an EmailAction by ID or raise ResourceNotFoundError."""
    action = await EmailAction.get(action_id)
    if not action or not isinstance(action, EmailAction):
        raise ResourceNotFoundError(
            message=f"Email action not found: {action_id}",
            details={"action_id": action_id},
        )
    return action
