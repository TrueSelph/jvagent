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

    When a response is updated, invalidates the branch cache entry for that
    question (if any) and the question node cache, so re-evaluation uses
    fresh branch resolution.

    Args:
        action: InterviewInteractAction instance for logging context
        session: Interview session
        response_key: Key of the response that was updated
    """
    from ..utils.cache_utils import BranchCache, QuestionNodeCache

    branch_cache = BranchCache(session)
    branch_cache.invalidate(response_key)
    logger.debug(
        "%s: Branch cache invalidated for response update '%s'",
        action.get_class_name(),
        response_key
    )

    question_cache = QuestionNodeCache(session)
    question_cache.invalidate()

    logger.debug(
        "%s: Coordinated cache invalidation for response update '%s'",
        action.get_class_name(),
        response_key
    )
