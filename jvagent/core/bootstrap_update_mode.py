"""Shared logic for persisted ``App.update_mode`` (run | merge | source)."""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

ALLOWED_APP_UPDATE_MODES = frozenset({"run", "merge", "source"})


def normalize_stored_update_mode(value: Optional[str]) -> str:
    """Return lowercase token or ``run`` if missing / invalid."""
    if value is None:
        return "run"
    s = str(value).strip().lower()
    if not s:
        return "run"
    if s in ALLOWED_APP_UPDATE_MODES:
        return s
    logger.warning("Invalid App.update_mode %r; treating as run", value)
    return "run"


def validate_admin_update_mode(value: str) -> str:
    """Validate API input; raises ValueError if not allowed."""
    if value is None or not str(value).strip():
        raise ValueError(
            f"update_mode must be one of {sorted(ALLOWED_APP_UPDATE_MODES)}"
        )
    s = str(value).strip().lower()
    if s not in ALLOWED_APP_UPDATE_MODES:
        raise ValueError(
            f"update_mode must be one of {sorted(ALLOWED_APP_UPDATE_MODES)}"
        )
    return s


def stored_update_mode_to_bootstrap_arg(stored: str) -> Optional[str]:
    """Map persisted ``App.update_mode`` to ``bootstrap_application_graph`` argument."""
    if stored == "run":
        return None
    if stored == "merge":
        return "merge"
    if stored == "source":
        return "source"
    return None


async def resolve_bootstrap_update_mode(
    cli_update_mode: Optional[str],
) -> Optional[str]:
    """CLI ``--update`` wins; else derive from ``App.update_mode`` when App exists."""
    from jvagent.core.app import App

    if cli_update_mode is not None:
        return cli_update_mode

    App.clear_cache()
    app = await App.get()
    if not app:
        return None

    stored = normalize_stored_update_mode(getattr(app, "update_mode", None))
    return stored_update_mode_to_bootstrap_arg(stored)


async def reset_app_update_mode_after_successful_bootstrap() -> None:
    """Set persisted mode to ``run`` after a full successful startup/bootstrap."""
    from jvagent.core.app import App, set_app_update_mode

    app = await App.get()
    if not app:
        return
    stored = normalize_stored_update_mode(getattr(app, "update_mode", None))
    if stored == "run":
        return
    await set_app_update_mode(app, "run")
