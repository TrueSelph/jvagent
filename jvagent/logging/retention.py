"""Background retention task for automatic log cleanup."""

import asyncio
import logging
from typing import Any, Dict, List, Optional

from jvagent.core.app import App
from jvagent.logging.service import LoggingService

logger = logging.getLogger(__name__)


class RetentionTask:
    """Background task for applying retention policies."""

    def __init__(self, logging_service: Optional[LoggingService] = None):
        """Initialize the retention task.

        Args:
            logging_service: Optional LoggingService instance. If not provided, creates one.
        """
        if logging_service is None:
            from jvagent.logging.service import get_logging_service
            logging_service = get_logging_service()
        self.logging_service = logging_service
        self._running = False
        self._task: Optional[asyncio.Task] = None

    async def run_once(self) -> Dict[str, Any]:
        """Run retention policy once for all agents.

        Returns:
            Dictionary with retention statistics
        """
        try:
            # Get all agents and apply retention for each
            from jvagent.core.agents import Agents
            from jvagent.core.agent import Agent
            
            agents_manager = await Agents.get()
            if not agents_manager:
                return {"processed": 0, "deleted": 0, "agents": []}

            # Get all agents
            agents = await agents_manager.nodes(node=Agent)
            if not agents:
                return {"processed": 0, "deleted": 0, "agents": []}

            results = []
            total_deleted = 0

            # Apply retention for each agent
            for agent in agents:
                result = await self.logging_service.apply_retention_policy(agent.id)
                deleted = result.get("deleted", 0)
                total_deleted += deleted

                results.append({
                    "agent_id": agent.id,
                    "agent_name": agent.name,
                    "deleted": deleted,
                })

            return {
                "processed": len(results),
                "deleted": total_deleted,
                "agents": results,
            }

        except Exception as e:
            logger.error(f"Error in retention task: {e}", exc_info=True)
            return {"processed": 0, "deleted": 0, "error": str(e)}

    async def start(self, interval_seconds: int = 86400) -> None:
        """Start the retention task running periodically.

        Args:
            interval_seconds: Interval between runs in seconds (default: 86400 = 1 day)
        """
        if self._running:
            logger.warning("Retention task is already running")
            return

        self._running = True
        self._task = asyncio.create_task(self._run_loop(interval_seconds))
        logger.info(f"Retention task started (interval: {interval_seconds}s)")

    async def stop(self) -> None:
        """Stop the retention task."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Retention task stopped")

    async def _run_loop(self, interval_seconds: int) -> None:
        """Run the retention task in a loop.

        Args:
            interval_seconds: Interval between runs
        """
        while self._running:
            try:
                await asyncio.sleep(interval_seconds)
                if self._running:
                    result = await self.run_once()
                    logger.info(f"Retention task completed: {result}")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in retention task loop: {e}", exc_info=True)


# Singleton instance
_retention_task: Optional[RetentionTask] = None


def get_retention_task() -> RetentionTask:
    """Get the singleton retention task instance.

    Returns:
        RetentionTask instance
    """
    global _retention_task
    if _retention_task is None:
        _retention_task = RetentionTask()
    return _retention_task

