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


def import_jvagent_endpoint_modules() -> None:
    """Import every first-party endpoint module in jvagent."""
    from jvagent.action import endpoints as _action_endpoints  # noqa: F401
    from jvagent.core import endpoints as _core_endpoints  # noqa: F401
    from jvagent.logging import endpoints as _logging_endpoints  # noqa: F401


__all__ = ["import_jvagent_endpoint_modules"]
