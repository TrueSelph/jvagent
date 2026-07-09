"""jvagent-specific environment helpers.

jvagent uses canonical live env reads from ``jvspatial.env`` for all generic
environment access. This module only contains jvagent-specific resolution
helpers that are not shared by jvspatial.
"""

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


def _read_env_with_dotenv(key: str) -> Optional[str]:
    """Read *key* from the app-root ``.env`` (then CWD ``.env``), then os.environ.

    Uses ``dotenv_values`` so the value resolves in child processes (e.g.
    ``uvicorn --reload``) where ``load_dotenv`` may not have run. Returns the
    stripped value, or ``None`` when unset/blank.
    """
    try:
        from dotenv import dotenv_values

        from jvagent.core.app_context import get_app_root

        root = get_app_root()
        for candidate in (os.path.join(root, ".env"), ".env"):
            if os.path.isfile(candidate):
                values = dotenv_values(candidate)
                val = values.get(key) if values else None
                if val and str(val).strip():
                    return str(val).strip()
                break
    except Exception as exc:
        logger.debug("env: .env read for %s failed: %s", key, exc)
    val = os.getenv(key)
    return str(val).strip() if val and str(val).strip() else None


def get_jvagent_app_id() -> Optional[str]:
    """Return JVAGENT_APP_ID if set (from .env or os.environ), else None."""
    return _read_env_with_dotenv("JVAGENT_APP_ID")


def get_jvagent_jvforge_base_url() -> Optional[str]:
    """Return JVAGENT_JVFORGE_BASE_URL if set (from .env or os.environ), else None."""
    val = _read_env_with_dotenv("JVAGENT_JVFORGE_BASE_URL")
    return val.rstrip("/") if val else None
