"""Static file server for the bundled jvchat UI (``jvagent chat``).

Serves the pre-built single-page app from ``jvagent/webui/dist/`` on its own
port, decoupled from the agent server (separate origin — see the security
rationale in ``docs/jvchat.md``). Stdlib only; no runtime Node dependency.

The agent server URL is injected into the served ``index.html`` at request
time via ``window.__JVCHAT_RUNTIME_CONFIG__`` so a single pre-built bundle can
target any agent without a rebuild.
"""

from __future__ import annotations

import json
import logging
import mimetypes
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def dist_dir() -> Path:
    """Absolute path to the bundled jvchat ``dist/`` directory."""
    return Path(__file__).resolve().parent / "dist"


def is_built() -> bool:
    """True when the UI has been built/bundled (``dist/index.html`` exists)."""
    return (dist_dir() / "index.html").is_file()


def _runtime_config_script(
    jvagent_url: Optional[str], jvforge_url: Optional[str]
) -> str:
    cfg = {}
    if jvagent_url:
        cfg["jvagentUrl"] = jvagent_url
    if jvforge_url:
        cfg["jvforgeUrl"] = jvforge_url
    if not cfg:
        return ""
    # json.dumps escapes </script> safely enough for inline use; also guard '<'.
    payload = json.dumps(cfg).replace("<", "\\u003c")
    return f"<script>window.__JVCHAT_RUNTIME_CONFIG__={payload};</script>"


def _build_handler(
    root: Path, jvagent_url: Optional[str], jvforge_url: Optional[str]
) -> type:
    inject = _runtime_config_script(jvagent_url, jvforge_url)
    index_path = root / "index.html"

    class _Handler(BaseHTTPRequestHandler):
        server_version = "jvagent-chat/1.0"

        def _headers(
            self, status: int, content_type: str, length: int, *, cache: bool
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(length))
            # Static hashed assets are immutable; HTML must never be cached so
            # the injected runtime config is always fresh.
            self.send_header(
                "Cache-Control",
                "public, max-age=31536000, immutable" if cache else "no-store",
            )
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()

        def _send_index(self) -> None:
            try:
                html = index_path.read_text(encoding="utf-8")
            except OSError:
                self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR, "UI not built")
                return
            if inject:
                # Inject right after <head> so it runs before the app bundle.
                lowered = html.lower()
                idx = lowered.find("<head>")
                if idx != -1:
                    cut = idx + len("<head>")
                    html = html[:cut] + inject + html[cut:]
                else:
                    html = inject + html
            body = html.encode("utf-8")
            self._headers(
                HTTPStatus.OK, "text/html; charset=utf-8", len(body), cache=False
            )
            if self.command != "HEAD":
                self.wfile.write(body)

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
            target = self._resolve(self.path)
            if target is None:
                # SPA fallback: unknown routes render the app (client routing).
                self._send_index()
                return
            ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
            data = target.read_bytes()
            self._headers(HTTPStatus.OK, ctype, len(data), cache=True)
            if self.command != "HEAD":
                self.wfile.write(data)

        def do_GET(self) -> None:  # noqa: N802
            self._serve()

        def do_HEAD(self) -> None:  # noqa: N802
            self._serve()

        def log_message(self, fmt: str, *args) -> None:
            logger.debug("jvchat %s - %s", self.address_string(), fmt % args)

    return _Handler


def serve(
    *,
    host: str = "127.0.0.1",
    port: int = 3000,
    jvagent_url: Optional[str] = None,
    jvforge_url: Optional[str] = None,
    open_browser: bool = True,
) -> None:
    """Serve the bundled jvchat UI until interrupted.

    Args:
        host: Bind address (default localhost — bind ``0.0.0.0`` deliberately).
        port: Port to listen on.
        jvagent_url: Agent server URL injected into the UI (``--url``). When
            omitted the UI falls back to its built-in default / the login screen.
        jvforge_url: Optional jvforge service URL.
        open_browser: Open the default browser at the served URL on startup.

    Raises:
        FileNotFoundError: If the UI has not been built (no bundled ``dist/``).
    """
    root = dist_dir()
    if not is_built():
        raise FileNotFoundError(
            "jvchat UI is not bundled (no jvagent/webui/dist/). This wheel was "
            "built without the UI, or you are in a source checkout — build it "
            "with `python scripts/build_jvchat.py`."
        )

    handler = _build_handler(root, jvagent_url, jvforge_url)
    httpd = ThreadingHTTPServer((host, port), handler)
    actual_port = httpd.server_address[1]
    url = f"http://{host}:{actual_port}"
    logger.info("jvchat UI serving at %s", url)
    if jvagent_url:
        logger.info("Targeting agent server: %s", jvagent_url)
    print(f"jvchat UI: {url}  (Ctrl+C to stop)")
    if jvagent_url:
        print(f"  → agent server: {jvagent_url}")

    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping jvchat UI.")
    finally:
        httpd.shutdown()
        httpd.server_close()
