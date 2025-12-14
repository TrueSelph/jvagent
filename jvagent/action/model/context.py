"""Context variables for model action observability."""

import contextvars
from typing import Optional

# Context variable to track current interaction_id for observability
current_interaction_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "current_interaction_id", default=None
)

# Context variable to track the calling action name for observability
current_action_name: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "current_action_name", default=None
)


def get_interaction_id() -> Optional[str]:
    """Get the current interaction ID from context.

    Returns:
        Interaction ID if set in context, None otherwise
    """
    return current_interaction_id.get()


def set_interaction_id(interaction_id: Optional[str]) -> None:
    """Set the current interaction ID in context.

    Args:
        interaction_id: Interaction ID to set
    """
    current_interaction_id.set(interaction_id)


def get_calling_action_name() -> Optional[str]:
    """Get the current calling action name from context.

    Returns:
        Calling action name (camelCase class name) if set in context, None otherwise
    """
    return current_action_name.get()


def set_calling_action_name(action_name: Optional[str]) -> None:
    """Set the current calling action name in context.

    Args:
        action_name: Action name (camelCase class name) to set (e.g., "PersonaAction", "ExampleInteractAction")
    """
    current_action_name.set(action_name)

