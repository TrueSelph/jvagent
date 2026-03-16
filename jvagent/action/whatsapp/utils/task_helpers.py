"""Background task utilities for WhatsApp action.

This module provides helper functions for managing background tasks with proper
exception handling. These utilities ensure that exceptions in fire-and-forget
tasks are logged rather than silently swallowed.

Respects JVSPATIAL_BACKGROUND_TASKS: when false (e.g. Lambda), returns None
instead of creating a task.
"""

import asyncio
import logging
from typing import Any, Coroutine, Optional

from jvspatial.config import is_background_tasks_enabled

logger = logging.getLogger(__name__)


def _handle_task_exception(task: asyncio.Task, name: str) -> None:
    """Handle exceptions from background tasks.

    Args:
        task: The completed task
        name: Name of the task for logging
    """
    try:
        task.result()
    except asyncio.CancelledError:
        # Task was cancelled, this is expected behavior
        pass
    except Exception as e:
        logger.error(f"Background task '{name}' failed: {e}", exc_info=True)


def create_background_task(
    coro: Coroutine[Any, Any, Any], name: str = "background"
) -> Optional[asyncio.Task]:
    """Create a background task with automatic exception logging.

    When JVSPATIAL_BACKGROUND_TASKS is false (e.g. Lambda), returns None
    without running the coroutine. Callers must handle None.

    Args:
        coro: Coroutine to run as a background task
        name: Descriptive name for the task (used in error logs)

    Returns:
        The created asyncio.Task, or None when background tasks disabled
    """
    if not is_background_tasks_enabled():
        logger.warning(
            "create_background_task called with JVSPATIAL_BACKGROUND_TASKS disabled; "
            "coroutine will not run. Set JVSPATIAL_BACKGROUND_TASKS=true for "
            "non-Lambda deployments."
        )
        return None
    task = asyncio.create_task(coro)
    task.add_done_callback(lambda t: _handle_task_exception(t, name))
    return task
