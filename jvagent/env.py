"""jvagent-specific environment helpers.

jvagent uses canonical live env reads from ``jvspatial.env`` for all generic
environment access. This module only contains jvagent-specific resolution
helpers that are not shared by jvspatial.
"""

import os
from typing import Optional


def get_jvagent_app_id() -> Optional[str]:
    """Return JVAGENT_APP_ID if set (from .env or os.environ), else None.

    Uses dotenv_values for .env so it works in child processes (e.g. uvicorn --reload)
    where load_dotenv may not have run.
    """
    try:
        from dotenv import dotenv_values

        from jvagent.core.app_context import get_app_root

        root = get_app_root()
        for candidate in (os.path.join(root, ".env"), ".env"):
            if os.path.isfile(candidate):
                values = dotenv_values(candidate)
                val = values.get("JVAGENT_APP_ID") if values else None
                if val and str(val).strip():
                    return str(val).strip()
                break
    except Exception:
        pass
    val = os.getenv("JVAGENT_APP_ID")
    return str(val).strip() if val and str(val).strip() else None


def get_jvagent_jvforge_base_url() -> Optional[str]:
    """Return JVAGENT_JVFORGE_BASE_URL if set (from .env or os.environ), else None."""
    try:
        from dotenv import dotenv_values

        from jvagent.core.app_context import get_app_root

        root = get_app_root()
        for candidate in (os.path.join(root, ".env"), ".env"):
            if os.path.isfile(candidate):
                values = dotenv_values(candidate)
                val = values.get("JVAGENT_JVFORGE_BASE_URL") if values else None
                if val and str(val).strip():
                    return str(val).strip().rstrip("/")
                break
    except Exception:
        pass
    val = os.getenv("JVAGENT_JVFORGE_BASE_URL")
    return str(val).strip().rstrip("/") if val and str(val).strip() else None
