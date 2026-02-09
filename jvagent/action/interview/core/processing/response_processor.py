"""Cache invalidation utilities for interview response updates."""

import logging
from typing import TYPE_CHECKING

from ..session.interview_session import InterviewSession

if TYPE_CHECKING:
    from jvagent.action.interview.interview_interact_action import InterviewInteractAction

logger = logging.getLogger(__name__)


async def invalidate_dependent_caches(
    action: "InterviewInteractAction",
    session: InterviewSession,
    response_key: str
) -> None:
    """Invalidate caches that depend on an updated response.

    When a response is updated, intelligently invalidates:
    1. Branch function cache entries that depend on this response
    2. Coordinate with question node cache invalidation

    This enables efficient re-evaluation: cached branch results are only
    re-executed if their dependencies have actually changed.

    Args:
        action: InterviewInteractAction instance for logging context
        session: Interview session
        response_key: Key of the response that was updated
    """
    from ..utils.cache_utils import BranchFunctionCache, QuestionNodeCache

    branch_cache = BranchFunctionCache(session)
    invalidated_branches = branch_cache.invalidate_by_response(response_key)

    if invalidated_branches:
        logger.debug(
            "%s: Response update '%s' invalidated %d branch cache entries: %s",
            action.get_class_name(),
            response_key,
            len(invalidated_branches),
            invalidated_branches
        )

    question_cache = QuestionNodeCache(session)
    question_cache.invalidate()

    logger.debug(
        "%s: Coordinated cache invalidation for response update '%s'",
        action.get_class_name(),
        response_key
    )
