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
    (tmp_path / "demo.html").write_text(
        "<!doctype html><html><head></head><body>demo host</body></html>",
        encoding="utf-8",
    )
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "app.js").write_text("console.log(1)", encoding="utf-8")
    return tmp_path


def _serve(
    root,
    frame_ancestors=messenger.DEFAULT_FRAME_ANCESTORS,
    sandbox_mode=False,
    agent_url="http://127.0.0.1:8000",
):
    handler = messenger._build_handler(
        root, frame_ancestors, sandbox_mode=sandbox_mode, agent_url=agent_url
    )
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


def test_root_serves_demo_host(tmp_path):
    """``/`` is the customer-site stand-in (demo.html), not the iframe app."""
    httpd = _serve(_make_dist(tmp_path))
    try:
        status, _, body = _get(httpd, "/")
        assert status == 200
        assert "demo host" in body.lower()
        status2, _, body2 = _get(httpd, "/app.html")
        assert status2 == 200
        assert "app" in body2.lower()
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


# ── sandbox mode tests ────────────────────────────────────────────────────────


def test_sandbox_route_returns_html(tmp_path):
    """GET / in sandbox mode returns 200 HTML containing the sandbox marker."""
    httpd = _serve(
        _make_dist(tmp_path), sandbox_mode=True, agent_url="http://127.0.0.1:8000"
    )
    try:
        status, headers, body = _get(httpd, "/")
        assert status == 200
        assert "text/html" in headers.get("Content-Type", "")
        assert "jvmessenger-sandbox" in body or "jvmessenger sandbox" in body.lower()
    finally:
        httpd.shutdown()


def test_sandbox_get_slash_sandbox_path(tmp_path):
    """GET /sandbox in sandbox mode also returns the sandbox page."""
    httpd = _serve(
        _make_dist(tmp_path), sandbox_mode=True, agent_url="http://127.0.0.1:8000"
    )
    try:
        status, _, body = _get(httpd, "/sandbox")
        assert status == 200
        assert "sandbox" in body.lower()
    finally:
        httpd.shutdown()


def test_sandbox_not_served_without_flag(tmp_path):
    """GET /sandbox without sandbox_mode falls back to app.html (SPA fallback), not the sandbox page."""
    httpd = _serve(_make_dist(tmp_path), sandbox_mode=False)
    try:
        status, _, body = _get(httpd, "/sandbox")
        assert status == 200
        # SPA fallback is app.html, not the sandbox login page.
        assert "f-email" not in body  # login field id only present in sandbox
        assert "demo host" not in body  # not demo.html either
    finally:
        httpd.shutdown()


def test_sandbox_html_contains_agent_url(tmp_path):
    """_sandbox_html() injects the provided agent_url into the generated page."""
    agent_url = "http://example.com:9000"
    html = messenger._sandbox_html(agent_url, "http://127.0.0.1:3100")
    assert agent_url in html


def test_sandbox_html_uses_email_not_username():
    """Login payload must use email (jvspatial UserLogin), not username."""
    html = messenger._sandbox_html("http://127.0.0.1:8000", "http://127.0.0.1:3100")
    assert 'id="f-email"' in html
    assert 'id="f-user"' not in html
    assert "email: email, password: password" in html
    assert "username: username" not in html


def test_sandbox_html_resolves_context_enabled():
    """Dropdown must treat missing enabled as true (jvspatial nests under context)."""
    html = messenger._sandbox_html("http://127.0.0.1:8000", "http://127.0.0.1:3100")
    assert "a.context && a.context.enabled" in html
    # Must not gate on bare !a.enabled (undefined → disabled for every agent).
    assert "if (!a.enabled)" not in html
    assert 'id="agent-select"' in html
    assert "agent-pills" not in html
    assert "renderAgentSelect" in html


def test_sandbox_html_has_config_panel():
    """Sandbox exposes embed config controls mapped to loader data-* attrs."""
    html = messenger._sandbox_html("http://127.0.0.1:8000", "http://127.0.0.1:3100")
    assert 'id="config-panel"' in html
    assert 'id="cfg-attachments"' in html
    assert 'id="cfg-quick-replies"' in html
    assert 'id="cfg-voice"' in html
    assert 'id="cfg-proactive"' in html
    assert "applyConfigToScript" in html
    assert "reinjectMessenger" in html
    assert "scheduleConfigApply" in html
    assert "jvmessenger_sandbox_config" in html
    assert "Auto-saves" in html
    assert "JVSPATIAL_JWT_SECRET_KEY" in html
    assert "cfg-req-note" in html
    assert "Attachments" in html and "Voice" in html and "Proactive" in html


def test_sandbox_page_not_embeddable(tmp_path):
    """Sandbox page must send X-Frame-Options: DENY — it is not a customer embed."""
    httpd = _serve(_make_dist(tmp_path), sandbox_mode=True)
    try:
        _, headers, _ = _get(httpd, "/")
        assert headers.get("X-Frame-Options") == "DENY"
        assert headers.get("Content-Security-Policy") is None
    finally:
        httpd.shutdown()


def test_loader_still_served_in_sandbox_mode(tmp_path):
    """loader.js must still be reachable in sandbox mode (sandbox page injects it)."""
    httpd = _serve(_make_dist(tmp_path), sandbox_mode=True)
    try:
        status, _, body = _get(httpd, "/loader.js")
        assert status == 200
        assert "loader" in body
    finally:
        httpd.shutdown()


def test_sandbox_cli_flags_parsed(monkeypatch, capsys):
    """--sandbox and --url flags are accepted by the CLI handler."""
    from jvagent.cli import messenger as messenger_cli

    calls = []

    class _Done(Exception):
        pass

    def fake_serve(**kwargs):
        calls.append(kwargs)
        raise _Done

    monkeypatch.setattr(messenger_cli, "is_built", lambda: True)
    monkeypatch.setattr(messenger_cli, "serve", fake_serve)

    with pytest.raises(_Done):
        messenger_cli.handle_messenger_command(
            ["--sandbox", "--url", "http://127.0.0.1:9999", "--no-browser"]
        )

    assert calls, "serve() was never called"
    assert calls[0]["sandbox_mode"] is True
    assert calls[0]["agent_url"] == "http://127.0.0.1:9999"
