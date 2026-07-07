"""App startup coordinator for jvagent."""

import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)

_startup_completed = False
# Monotonic timestamp of the last failed startup attempt. A failed attempt
# backs off for ``_STARTUP_RETRY_SECONDS`` instead of re-running the full
# action init on every call — otherwise a persistently-failing action turns
# each request into a retry storm.
_startup_last_failure: float = 0.0
_STARTUP_RETRY_SECONDS: float = 30.0
_repair_scheduler = None


async def start_repair_scheduler() -> None:
    """Start the optional periodic repair scheduler.

    Controlled by the ``JVAGENT_REPAIR_SCHEDULE_CRON`` environment variable.
    When set, an APScheduler ``AsyncIOScheduler`` job is created that calls
    ``repair_agent_graph(max_seconds=5)`` on the specified cron schedule.

    Example::

        JVAGENT_REPAIR_SCHEDULE_CRON="*/5 * * * *"   # every 5 minutes

    If APScheduler is not installed the function logs a warning and returns
    without raising.

    In serverless runtimes (Lambda, Cloud Run, Azure Functions) this function
    is a no-op: background scheduled tasks cannot persist across invocations
    and would consume resources unnecessarily on warm containers.  Repair
    should be driven externally (EventBridge, Cloud Scheduler, etc.) by
    calling ``POST /graph/repair`` on a schedule instead.
    """
    global _repair_scheduler
    import os

    from jvspatial.runtime.serverless import is_serverless_mode

    if is_serverless_mode():
        cron = os.environ.get("JVAGENT_REPAIR_SCHEDULE_CRON", "").strip()
        if cron:
            logger.info(
                "JVAGENT_REPAIR_SCHEDULE_CRON=%r is set but will be ignored in serverless "
                "mode.  Trigger POST /graph/repair externally (e.g. via EventBridge).",
                cron,
            )
        return

    cron = os.environ.get("JVAGENT_REPAIR_SCHEDULE_CRON", "").strip()
    if not cron:
        return

    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        from apscheduler.triggers.cron import CronTrigger
    except ImportError:
        logger.warning(
            "JVAGENT_REPAIR_SCHEDULE_CRON is set but 'apscheduler' is not installed; "
            "skipping repair scheduler."
        )
        return

    if _repair_scheduler is not None:
        return  # Already running

    async def _repair_job() -> None:
        try:
            from jvagent.core.graph_repair import repair_agent_graph

            result = await repair_agent_graph(max_seconds=5)
            logger.debug("Scheduled repair step: %s", result.get("status"))
        except Exception:
            logger.warning("Scheduled repair step failed", exc_info=True)

    try:
        parts = cron.split()
        if len(parts) == 5:
            minute, hour, day, month, day_of_week = parts
        else:
            logger.warning(
                "JVAGENT_REPAIR_SCHEDULE_CRON: invalid cron expression %r", cron
            )
            return

        scheduler = AsyncIOScheduler()
        trigger = CronTrigger(
            minute=minute,
            hour=hour,
            day=day,
            month=month,
            day_of_week=day_of_week,
        )
        scheduler.add_job(
            _repair_job, trigger, id="jvagent_repair", replace_existing=True
        )
        scheduler.start()
        _repair_scheduler = scheduler
        logger.info("Repair scheduler started with cron=%r", cron)
    except Exception:
        logger.warning("Failed to start repair scheduler", exc_info=True)


async def stop_repair_scheduler() -> None:
    """Shut down the repair scheduler (called on app shutdown)."""
    global _repair_scheduler
    if _repair_scheduler is not None:
        try:
            _repair_scheduler.shutdown(wait=False)
        except Exception:
            pass
        _repair_scheduler = None


async def run_app_startup() -> bool:
    """Run app startup sequence.

    This should be called once when the app process starts, before
    handling any requests. It ensures all actions are properly
    initialized.

    Returns:
        True if startup succeeded, False otherwise
    """
    global _startup_completed, _startup_last_failure

    if _startup_completed:
        return True
    if (
        _startup_last_failure
        and (time.monotonic() - _startup_last_failure) < _STARTUP_RETRY_SECONDS
    ):
        # A recent attempt failed; don't re-run the full action init on every
        # call — wait out the backoff window first.
        return False

    try:
        from jvagent.core.app import App

        app = await App.get()
        if not app:
            logger.warning("App not found during startup")
            _startup_last_failure = time.monotonic()
            return False

        # Initialize all actions
        results = await app.initialize_actions()

        failed_count = sum(1 for success in results.values() if not success)
        if failed_count > 0:
            logger.warning(
                f"Startup completed with {failed_count} action(s) failing initialization"
            )
            _startup_last_failure = time.monotonic()
            return False
        else:
            logger.info("App startup completed successfully")
            _startup_completed = True
            _startup_last_failure = 0.0

            # Start the optional periodic repair scheduler (no-op if env var unset).
            await start_repair_scheduler()

            return True

    except Exception as e:
        logger.error(f"Error during app startup: {e}", exc_info=True)
        _startup_last_failure = time.monotonic()
        return False
