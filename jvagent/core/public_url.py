"""Canonical public origin for webhooks, OAuth callbacks, and absolute media URLs."""

import os


def get_public_base_url() -> str:
    """Return ``JVAGENT_PUBLIC_BASE_URL`` (stripped), or empty string if unset."""
    v = os.environ.get("JVAGENT_PUBLIC_BASE_URL")
    return str(v).strip() if v else ""
