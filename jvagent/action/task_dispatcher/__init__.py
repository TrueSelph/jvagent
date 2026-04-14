"""Task dispatcher for proactive tasks."""

# Import endpoints to ensure they are discovered by the API
from jvagent.action.task_dispatcher import endpoints  # noqa: F401
from jvagent.action.task_dispatcher.task_dispatcher import TaskDispatcher

__all__ = ["TaskDispatcher"]
