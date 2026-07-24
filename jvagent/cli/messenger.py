"""``jvagent messenger`` — serve the embeddable jvmessenger popup chat on its own port.

The messenger is embedded on third-party customer sites with a single ``<script>``
tag pointing at ``<this-server>/loader.js``. It runs on its own static server,
decoupled from the agent server, and must be framable by customer sites — so it
uses a configurable CSP ``frame-ancestors`` allowlist instead of
``X-Frame-Options: DENY``. Point the messenger's chat calls at any agent by setting
``data-agent-url`` on the embed script tag.
"""

from __future__ import annotations

import argparse
import sys
from typing import List

from jvagent.messenger import DEFAULT_FRAME_ANCESTORS, is_built, serve


def handle_messenger_command(args: List[str]) -> None:
    """Parse ``jvagent messenger`` flags and serve the bundled messenger assets."""
    parser = argparse.ArgumentParser(
        prog="jvagent messenger",
        description="Serve the embeddable jvmessenger popup chat on its own port.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=3100,
        help="Port to serve the messenger on (default: 3100).",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Bind address (default: 127.0.0.1). Use 0.0.0.0 to expose on the network.",
    )
    parser.add_argument(
        "--frame-ancestors",
        default=DEFAULT_FRAME_ANCESTORS,
        help="CSP frame-ancestors allowlist controlling which host origins may "
        "embed the messenger (default: '*'). Set to explicit customer origins in "
        'production, e.g. "https://acme.com https://shop.acme.com".',
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not open a browser window on startup.",
    )
    ns = parser.parse_args(args)

    if not is_built():
        print(
            "jvmessenger is not bundled in this install.\n"
            "  • If you installed from PyPI, this build shipped without the messenger.\n"
            "  • In a source checkout, build it first:\n"
            "        python scripts/build_jvmessenger.py\n",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        serve(
            host=ns.host,
            port=ns.port,
            frame_ancestors=ns.frame_ancestors,
            open_browser=not ns.no_browser,
        )
    except OSError as exc:
        print(
            f"Failed to start jvmessenger on {ns.host}:{ns.port}: {exc}",
            file=sys.stderr,
        )
        sys.exit(1)
