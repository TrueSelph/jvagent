"""App startup coordinator for jvagent."""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

_startup_completed = False


async def run_app_startup() -> bool:
    """Run app startup sequence.

    This should be called once when the app process starts, before
    handling any requests. It ensures all actions are properly
    initialized.

    Returns:
        True if startup succeeded, False otherwise
    """
    global _startup_completed

    if _startup_completed:
        return True

    try:
        from jvagent.core.app import App

        app = await App.get()
        if not app:
            logger.warning("App not found during startup")
            return False

        # Initialize all actions
        results = await app.initialize_actions()

        failed_count = sum(1 for success in results.values() if not success)
        if failed_count > 0:
            logger.warning(
                f"Startup completed with {failed_count} action(s) failing initialization"
            )
        else:
            logger.info("App startup completed successfully")

        _startup_completed = True
        return True

    except Exception as e:
        logger.error(f"Error during app startup: {e}", exc_info=True)
        return False
