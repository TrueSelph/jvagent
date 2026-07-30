"""Quick-reply suggestions action.

Emits LLM-generated quick-reply chips (``metadata.suggestions``) after each
reply, rendered as agent-driven quick replies by the embeddable messenger.
"""

from .suggestions_interact_action import (
    SuggestionsInteractAction,
    is_data_request,
    needs_user_specifics,
    parse_suggestions,
)

__all__ = [
    "SuggestionsInteractAction",
    "is_data_request",
    "needs_user_specifics",
    "parse_suggestions",
]
