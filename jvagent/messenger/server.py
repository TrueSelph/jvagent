"""Static file server for the embeddable jvmessenger popup chat (``jvagent messenger``).

Serves the pre-built messenger bundle from ``jvagent/messenger/dist/`` on its own port.
Stdlib only; no runtime Node dependency. Two artifacts are served:

* ``loader.js`` — a tiny framework-less script the customer embeds with a single
  ``<script>`` tag on their site. It injects a launcher button and, on open, the
  chat ``<iframe>``.
* ``app.html`` + hashed JS/CSS — the React chat app that runs *inside* the iframe.

Unlike the standalone jvchat SPA (``jvagent/webui/server.py``), this server MUST
allow its pages to be framed by third-party customer sites. It therefore does
**not** send ``X-Frame-Options: DENY``; instead it sends a configurable
``Content-Security-Policy: frame-ancestors`` allowlist. ``loader.js`` is served
with permissive CORS so it can be fetched cross-origin from any host page.

All messenger configuration (agent URL, agent id, theme, avatar, feature toggles)
is supplied by the host page via the loader's ``data-*`` attributes and passed
to the iframe over an origin-checked ``postMessage`` handshake — never via the
URL — so there is no runtime config injection here.
"""

from __future__ import annotations

import logging
import mimetypes
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Default frame-ancestors allowlist. ``*`` permits embedding on any site, which
# is convenient for local development but should be tightened to the customer's
# explicit origins in production (see docs/jvmessenger.md).
DEFAULT_FRAME_ANCESTORS = "*"


def dist_dir() -> Path:
    """Absolute path to the bundled jvmessenger ``dist/`` directory."""
    return Path(__file__).resolve().parent / "dist"


def is_built() -> bool:
    """True when the messenger has been built/bundled (``dist/loader.js`` exists)."""
    return (dist_dir() / "loader.js").is_file() and (dist_dir() / "app.html").is_file()


def _build_handler(root: Path, frame_ancestors: str) -> type:
    csp = f"frame-ancestors {frame_ancestors}"

    class _Handler(BaseHTTPRequestHandler):
        server_version = "jvagent-messenger/1.0"

        def _headers(
            self,
            status: int,
            content_type: str,
            length: int,
            *,
            cache: bool,
            embeddable: bool,
            cors: bool = False,
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(length))
            # Hashed assets are immutable; HTML/loader must stay fresh so config
            # and framing changes propagate without a stale cache.
            self.send_header(
                "Cache-Control",
                "public, max-age=31536000, immutable" if cache else "no-store",
            )
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            # Embeddable pages use CSP frame-ancestors (allowlist) INSTEAD of
            # X-Frame-Options: DENY — the latter would break iframe embedding.
            if embeddable:
                self.send_header("Content-Security-Policy", csp)
            else:
                self.send_header("X-Frame-Options", "DENY")
            # loader.js is fetched cross-origin from the customer's site.
            if cors:
                self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()

        def _send_file(self, target: Path) -> None:
            name = target.name
            is_loader = name == "loader.js"
            is_html = target.suffix == ".html"
            # loader.js + the iframe app page must be framable and uncached;
            # fingerprinted assets under assets/ are immutable.
            embeddable = is_loader or is_html
            cache = not (is_loader or is_html)
            ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
            data = target.read_bytes()
            self._headers(
                HTTPStatus.OK,
                ctype,
                len(data),
                cache=cache,
                embeddable=embeddable,
                cors=is_loader,
            )
            if self.command != "HEAD":
                self.wfile.write(data)

        def _send_app_index(self) -> None:
            """SPA fallback: serve app.html for unknown (client-routed) paths."""
            target = root / "app.html"
            if not target.is_file():
                self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR, "Messenger not built")
                return
            self._send_file(target)

        def _resolve(self, path: str) -> Optional[Path]:
            rel = path.split("?", 1)[0].split("#", 1)[0].lstrip("/")
            if not rel:
                return None
            target = (root / rel).resolve()
            # Path-traversal guard: must stay under root.
            if target != root and root not in target.parents:
                return None
            return target if target.is_file() else None

        def _serve(self) -> None:
            # Root path serves the iframe app entry.
            if self.path.split("?", 1)[0] in ("/", "/app.html"):
                self._send_app_index()
                return
            target = self._resolve(self.path)
            if target is None:
                self._send_app_index()
                return
            self._send_file(target)

        def do_GET(self) -> None:  # noqa: N802
            self._serve()

        def do_HEAD(self) -> None:  # noqa: N802
            self._serve()

        def log_message(self, fmt: str, *args) -> None:
            logger.debug("jvmessenger %s - %s", self.address_string(), fmt % args)

    return _Handler


def serve(
    *,
    host: str = "127.0.0.1",
    port: int = 3100,
    frame_ancestors: str = DEFAULT_FRAME_ANCESTORS,
    open_browser: bool = True,
) -> None:
    """Serve the bundled jvmessenger assets until interrupted.

    Args:
        host: Bind address (default localhost — bind ``0.0.0.0`` deliberately).
        port: Port to listen on (default 3100, distinct from ``jvagent chat``).
        frame_ancestors: CSP ``frame-ancestors`` allowlist controlling which host
            origins may embed the messenger iframe. ``*`` (default) allows any; set
            to the customer's explicit origins in production.
        open_browser: Open the default browser at the served URL on startup.

    Raises:
        FileNotFoundError: If the messenger has not been built (no bundled ``dist/``).
    """
    root = dist_dir()
    if not is_built():
        raise FileNotFoundError(
            "jvmessenger is not bundled (no jvagent/messenger/dist/). This wheel was "
            "built without the messenger, or you are in a source checkout — build "
            "it with `python scripts/build_jvmessenger.py`."
        )

    handler = _build_handler(root, frame_ancestors)
    httpd = ThreadingHTTPServer((host, port), handler)
    actual_port = httpd.server_address[1]
    url = f"http://{host}:{actual_port}"
    logger.info("jvmessenger serving at %s", url)
    print(f"jvmessenger: {url}  (Ctrl+C to stop)")
    print(f"  → loader: {url}/loader.js")
    print(f"  → frame-ancestors: {frame_ancestors}")

    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping jvmessenger.")
    finally:
        httpd.shutdown()
        httpd.server_close()
