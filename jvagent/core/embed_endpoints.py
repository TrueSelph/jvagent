"""Shared importer for jvagent's @endpoint-decorated route modules.

Importing each module in this helper triggers ``@endpoint`` decorator
side effects, which register the routes either:

* immediately on the current jvspatial ``Server`` (if one is already
  installed via ``set_current_server``), or
* into jvspatial's deferred-endpoint registry, which gets flushed when
  the host instantiates its ``Server``.

Both the standalone CLI (``jvagent.cli.server_config``) and the embed
surface (``jvagent.embed``) call into this single function so the set of
"first-party" endpoint modules stays in lockstep across run modes.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def import_jvagent_endpoint_modules() -> None:
    """Import every first-party endpoint module in jvagent.

    Imports are best-effort: the optional Google OAuth module is allowed
    to be absent (its action package is only present when the google
    integration is installed).
    """
    from jvagent.action import endpoints as _action_endpoints  # noqa: F401
    from jvagent.core import endpoints as _core_endpoints  # noqa: F401
    from jvagent.logging import endpoints as _logging_endpoints  # noqa: F401

    try:
        import jvagent.action.google.endpoints as _google_oauth  # noqa: F401
    except ImportError:
        logger.debug(
            "jvagent.action.google.endpoints not importable; skipping optional google OAuth routes"
        )


__all__ = ["import_jvagent_endpoint_modules"]
