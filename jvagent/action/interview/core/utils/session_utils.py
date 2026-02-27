"""Session utility functions.

Extracted from duplicate session cleanup and helper code.
"""

import logging
from typing import TYPE_CHECKING, Dict, List, Optional

if TYPE_CHECKING:
    from jvagent.action.interact.interact_walker import InteractWalker

    from ..session.interview_session import InterviewSession

logger = logging.getLogger(__name__)


def get_graph_order(question_graph: List[dict]) -> Dict[str, int]:
    """Get mapping of question names to their index in question_graph.

    Args:
        question_graph: List of question configuration dicts

    Returns:
        Dict mapping question name to index (0-based). Unknown names use 999.
    """
    return {q["name"]: i for i, q in enumerate(question_graph) if q.get("name")}


async def cleanup_session(
    session: "InterviewSession",
    visitor: Optional["InteractWalker"] = None,
    action_name: Optional[str] = None,
) -> None:
    """Cleanup session data and edges.

    Centralized session cleanup logic extracted from duplicate code.
    Removes session from graph and clears visitor reference.

    Uses session.delete(cascade=False) for direct removal. If that fails,
    falls back to context.delete() to ensure the session is removed.

    Args:
        session: Interview session to cleanup
        visitor: Optional InteractWalker to clear session reference from
        action_name: Optional action name for logging
    """
    log_name = action_name or "InterviewAction"
    try:
        # Use cascade=False for simpler, more reliable deletion of session and its edges
        await session.delete(cascade=False)
        if visitor:
            visitor.interview_session = None
    except Exception as e:
        logger.warning(
            f"{log_name}: Session delete failed ({e}), attempting direct context delete"
        )
        try:
            context = await session.get_context()
            # Clear edge_ids to satisfy context.delete's recursion guard
            if hasattr(session, "edge_ids") and session.edge_ids:
                session.edge_ids = []
            await context.delete(session, cascade=False)
            if visitor:
                visitor.interview_session = None
            logger.info(f"{log_name}: Session removed via fallback context delete")
        except Exception as fallback_e:
            logger.error(
                f"{log_name}: Failed to cleanup session: {fallback_e}",
                exc_info=True,
            )
            raise


def sort_fields_by_question_order(
    fields: List[str], session: "InterviewSession"
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
