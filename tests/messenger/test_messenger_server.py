"""Tests for the embeddable jvmessenger static server (`jvagent messenger`).

The messenger server mirrors the jvchat server (`tests/webui/test_webui_server.py`)
but must diverge on framing: its embeddable pages (loader.js, app.html) carry a
CSP ``frame-ancestors`` allowlist and NO ``X-Frame-Options: DENY`` so customer
sites can frame them, while hashed assets stay ``DENY`` + immutable.
"""

import threading
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from jvagent.messenger import server as messenger


def _make_dist(tmp_path):
    (tmp_path / "loader.js").write_text("console.log('loader')", encoding="utf-8")
    (tmp_path / "app.html").write_text(
        "<!doctype html><html><head></head><body>app</body></html>",
        encoding="utf-8",
    )
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "app.js").write_text("console.log(1)", encoding="utf-8")
    return tmp_path


def _serve(root, frame_ancestors=messenger.DEFAULT_FRAME_ANCESTORS):
    handler = messenger._build_handler(root, frame_ancestors)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def _get(httpd, path):
    port = httpd.server_address[1]
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}") as r:
        return r.status, dict(r.headers), r.read().decode("utf-8")


def test_loader_is_embeddable_and_cors_enabled(tmp_path):
    httpd = _serve(_make_dist(tmp_path), frame_ancestors="https://acme.com")
    try:
        status, headers, body = _get(httpd, "/loader.js")
        assert status == 200
        assert "loader" in body
        # Embeddable: CSP allowlist, never X-Frame-Options: DENY.
        assert (
            headers.get("Content-Security-Policy") == "frame-ancestors https://acme.com"
        )
        assert headers.get("X-Frame-Options") is None
        # Fetched cross-origin from the customer's page.
        assert headers.get("Access-Control-Allow-Origin") == "*"
        # Must stay fresh so config/framing changes propagate.
        assert headers.get("Cache-Control") == "no-store"
    finally:
        httpd.shutdown()


def test_app_html_is_framable(tmp_path):
    httpd = _serve(_make_dist(tmp_path))
    try:
        status, headers, _ = _get(httpd, "/app.html")
        assert status == 200
        assert headers.get("Content-Security-Policy") == "frame-ancestors *"
        assert headers.get("X-Frame-Options") is None
    finally:
        httpd.shutdown()


def test_root_serves_app_entry(tmp_path):
    httpd = _serve(_make_dist(tmp_path))
    try:
        status, _, body = _get(httpd, "/")
        assert status == 200
        assert "app" in body.lower()
    finally:
        httpd.shutdown()


def test_spa_fallback_for_unknown_route(tmp_path):
    httpd = _serve(_make_dist(tmp_path))
    try:
        status, _, body = _get(httpd, "/some/deep/route")
        assert status == 200
        assert "<!doctype html>" in body.lower()
    finally:
        httpd.shutdown()


def test_hashed_asset_immutable_and_not_framable(tmp_path):
    httpd = _serve(_make_dist(tmp_path))
    try:
        status, headers, body = _get(httpd, "/assets/app.js")
        assert status == 200
        assert "console.log" in body
        assert "immutable" in headers.get("Cache-Control", "")
        # Non-HTML assets are not meant to be framed.
        assert headers.get("X-Frame-Options") == "DENY"
        assert headers.get("Content-Security-Policy") is None
    finally:
        httpd.shutdown()


def test_path_traversal_does_not_leak(tmp_path):
    secret = tmp_path.parent / "secret.txt"
    secret.write_text("TOPSECRET", encoding="utf-8")
    httpd = _serve(_make_dist(tmp_path))
    try:
        _, _, body = _get(httpd, "/../secret.txt")
        assert "TOPSECRET" not in body  # falls back to app.html, never the file
    finally:
        httpd.shutdown()


def test_is_built_requires_both_artifacts(tmp_path, monkeypatch):
    monkeypatch.setattr(messenger, "dist_dir", lambda: tmp_path)
    assert messenger.is_built() is False
    (tmp_path / "loader.js").write_text("x", encoding="utf-8")
    assert messenger.is_built() is False  # app.html still missing
    (tmp_path / "app.html").write_text("x", encoding="utf-8")
    assert messenger.is_built() is True


def test_messenger_command_errors_without_bundle(monkeypatch, capsys):
    from jvagent.cli import messenger as messenger_cli

    monkeypatch.setattr(messenger_cli, "is_built", lambda: False)
    with pytest.raises(SystemExit) as exc:
        messenger_cli.handle_messenger_command([])
    assert exc.value.code == 1
    assert "not bundled" in capsys.readouterr().err


def test_messenger_registered_in_cli_dispatch():
    from jvagent.cli.main import DISPATCH

    assert "messenger" in DISPATCH
