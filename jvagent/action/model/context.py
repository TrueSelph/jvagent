"""Context variables for model action observability."""

import contextvars
from typing import Optional

# Context variable to track current interaction_id for observability
current_interaction_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "current_interaction_id", default=None
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

