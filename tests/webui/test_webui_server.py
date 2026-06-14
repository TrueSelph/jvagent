"""Tests for the bundled jvchat static server (`jvagent chat`)."""

import threading
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from jvagent.webui import server as webui


def _make_dist(tmp_path):
    (tmp_path / "index.html").write_text(
        "<!doctype html><html><head></head><body>app</body></html>",
        encoding="utf-8",
    )
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "main.js").write_text("console.log(1)", encoding="utf-8")
    return tmp_path


def _serve(root, jvagent_url=None, jvforge_url=None):
    handler = webui._build_handler(root, jvagent_url, jvforge_url)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def _get(httpd, path):
    port = httpd.server_address[1]
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}") as r:
        return r.status, dict(r.headers), r.read().decode("utf-8")


def test_runtime_config_injected_into_index(tmp_path):
    httpd = _serve(_make_dist(tmp_path), jvagent_url="http://agent.example:8000")
    try:
        status, headers, body = _get(httpd, "/")
        assert status == 200
        assert "__JVCHAT_RUNTIME_CONFIG__" in body
        assert "http://agent.example:8000" in body
        assert headers.get("Cache-Control") == "no-store"
        assert headers.get("X-Content-Type-Options") == "nosniff"
        assert headers.get("X-Frame-Options") == "DENY"
    finally:
        httpd.shutdown()


def test_no_injection_without_url(tmp_path):
    httpd = _serve(_make_dist(tmp_path))
    try:
        _, _, body = _get(httpd, "/")
        assert "__JVCHAT_RUNTIME_CONFIG__" not in body
    finally:
        httpd.shutdown()


def test_spa_fallback_for_unknown_route(tmp_path):
    httpd = _serve(_make_dist(tmp_path))
    try:
        status, _, body = _get(httpd, "/debug")
        assert status == 200
        assert "<!doctype html>" in body.lower()
    finally:
        httpd.shutdown()


def test_asset_served_with_immutable_cache(tmp_path):
    httpd = _serve(_make_dist(tmp_path))
    try:
        status, headers, body = _get(httpd, "/assets/main.js")
        assert status == 200
        assert "console.log" in body
        assert "immutable" in headers.get("Cache-Control", "")
    finally:
        httpd.shutdown()


def test_path_traversal_does_not_leak(tmp_path):
    secret = tmp_path.parent / "secret.txt"
    secret.write_text("TOPSECRET", encoding="utf-8")
    httpd = _serve(_make_dist(tmp_path))
    try:
        _, _, body = _get(httpd, "/../secret.txt")
        assert "TOPSECRET" not in body  # falls back to index, never the file
    finally:
        httpd.shutdown()


def test_runtime_config_script_empty_without_urls():
    assert webui._runtime_config_script(None, None) == ""


def test_runtime_config_script_escapes_angle_bracket():
    out = webui._runtime_config_script("http://x/</script><b>", None)
    # The injected payload (between the wrapper tags) must contain no raw '<'
    # so a malicious URL cannot break out of the inline <script>.
    inner = out[len("<script>") : -len("</script>")]
    assert "<" not in inner
    assert "\\u003c" in out


def test_chat_command_errors_without_bundle(monkeypatch, capsys):
    from jvagent.cli import chat

    monkeypatch.setattr(chat, "is_built", lambda: False)
    with pytest.raises(SystemExit) as exc:
        chat.handle_chat_command([])
    assert exc.value.code == 1
    assert "not bundled" in capsys.readouterr().err
