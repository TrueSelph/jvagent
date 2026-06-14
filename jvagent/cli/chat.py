"""``jvagent chat`` — serve the bundled jvchat UI on its own port.

The UI runs as a standalone static server, decoupled from the agent server
(separate origin) so it never widens the agent's public attack surface. Point
it at any agent with ``--url``.
"""

from __future__ import annotations

import argparse
import sys
from typing import List, Optional

from jvagent.webui import is_built, serve


def handle_chat_command(args: List[str]) -> None:
    """Parse ``jvagent chat`` flags and serve the bundled UI."""
    parser = argparse.ArgumentParser(
        prog="jvagent chat",
        description="Serve the bundled jvchat web UI on its own port.",
    )
    parser.add_argument(
        "--url",
        dest="jvagent_url",
        default=None,
        help="Agent server URL the UI should talk to (e.g. http://localhost:8000 "
        "or https://my-agent.example.com). Injected at runtime; no rebuild needed. "
        "If omitted, the UI uses its built-in default / the login screen.",
    )
    parser.add_argument(
        "--jvforge-url",
        dest="jvforge_url",
        default=None,
        help="Optional jvforge service URL to inject.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=3000,
        help="Port to serve the UI on (default: 3000).",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Bind address (default: 127.0.0.1). Use 0.0.0.0 to expose on the network.",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not open a browser window on startup.",
    )
    ns = parser.parse_args(args)

    if not is_built():
        print(
            "jvchat UI is not bundled in this install.\n"
            "  • If you installed from PyPI, this build shipped without the UI.\n"
            "  • In a source checkout, build it first:\n"
            "        python scripts/build_jvchat.py\n",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        serve(
            host=ns.host,
            port=ns.port,
            jvagent_url=ns.jvagent_url,
            jvforge_url=ns.jvforge_url,
            open_browser=not ns.no_browser,
        )
    except OSError as exc:
        print(
            f"Failed to start jvchat UI on {ns.host}:{ns.port}: {exc}", file=sys.stderr
        )
        sys.exit(1)
