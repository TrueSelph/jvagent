"""Session utility functions.

Extracted from duplicate session cleanup and helper code.
"""

import logging
from typing import TYPE_CHECKING, List, Optional

if TYPE_CHECKING:
    from ..interview_session import InterviewSession
    from jvagent.action.interact.interact_walker import InteractWalker

logger = logging.getLogger(__name__)


async def cleanup_session(
    session: "InterviewSession",
    visitor: Optional["InteractWalker"] = None,
    action_name: Optional[str] = None
) -> None:
    """Cleanup session data and edges.
    
    Centralized session cleanup logic extracted from duplicate code.
    Removes session from graph and clears visitor reference.
    
    Args:
        session: Interview session to cleanup
        visitor: Optional InteractWalker to clear session reference from
        action_name: Optional action name for logging
    """
    try:
        await session.cleanup()
        if visitor:
            visitor.interview_session = None
    except Exception as e:
        log_name = action_name or "InterviewAction"
        logger.error(f"{log_name}: Failed to cleanup session: {e}", exc_info=True)


def sort_fields_by_question_order(
    fields: List[str],
    session: "InterviewSession"
) -> List[str]:
    """Sort fields by their position in question_graph.

    This ensures fields are processed in the logical order defined by the
    interview schema, which is important for conditional edge evaluation.

    Extracted from duplicate code in response_processor.py and interview_interact_action.py.

    Args:
        fields: List of field names to sort
        session: Interview session with question_graph

    Returns:
        Sorted list of field names in question_graph order
    """
    # Create a map of field name to index position
    field_to_index = {}
    for idx, question_config in enumerate(session.question_graph):
        field_name = question_config.get("name", "")
        if field_name:
            field_to_index[field_name] = idx
    
    # Sort fields by their index, unknown fields go to the end
    def get_sort_key(field: str) -> int:
        return field_to_index.get(field, len(session.question_graph))
    
    return sorted(fields, key=get_sort_key)
