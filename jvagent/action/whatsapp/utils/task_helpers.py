"""Background task utilities for WhatsApp action.

This module provides helper functions for managing background tasks with proper
exception handling. These utilities ensure that exceptions in fire-and-forget
tasks are logged rather than silently swallowed.
"""

import asyncio
import logging
from typing import Any, Coroutine

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
) -> asyncio.Task:
    """Create a background task with automatic exception logging.

    This wrapper ensures that any exceptions in fire-and-forget tasks
    are logged rather than silently swallowed.

    Args:
        coro: Coroutine to run as a background task
        name: Descriptive name for the task (used in error logs)

    Returns:
        The created asyncio.Task
    """
    task = asyncio.create_task(coro)
    task.add_done_callback(lambda t: _handle_task_exception(t, name))
    return task
