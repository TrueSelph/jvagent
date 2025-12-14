"""Context variables for model action observability."""

import contextvars
from typing import Optional

# Context variable to track current interaction_id for observability
current_interaction_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "current_interaction_id", default=None
)

# Context variable to track the calling action label for observability
current_calling_action_label: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "current_calling_action_label", default=None
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


def get_calling_action_label() -> Optional[str]:
    """Get the current calling action label from context.

    Returns:
        Calling action label if set in context, None otherwise
    """
    return current_calling_action_label.get()


def set_calling_action_label(action_label: Optional[str]) -> None:
    """Set the current calling action label in context.

    Args:
        action_label: Action label to set (e.g., "PersonaAction", "ExampleInteractAction")
    """
    current_calling_action_label.set(action_label)

