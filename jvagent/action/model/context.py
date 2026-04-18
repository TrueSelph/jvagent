"""Context variables for model action observability."""

import contextvars
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from jvagent.memory.interaction import Interaction

# Context variable to track current interaction for observability
current_interaction: contextvars.ContextVar[Optional[Any]] = contextvars.ContextVar(
    "current_interaction", default=None
)

# Context variable to track the calling action name for observability
current_action_name: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "current_action_name", default=None
)


def get_interaction() -> Optional[Any]:
    """Get the current interaction object from context.

    Returns:
        Interaction object if set in context, None otherwise
    """
    return current_interaction.get()


def set_interaction(interaction: Optional[Any]) -> None:
    """Set the current interaction object in context.

    Args:
        interaction: Interaction object to set
    """
    current_interaction.set(interaction)


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
