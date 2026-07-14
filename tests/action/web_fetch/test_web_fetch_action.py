"""WebFetchAction: SSRF/scheme guards, HTML→markdown extraction, truncation,
content-type rejection, redirect re-validation, and the web_fetch__fetch tool.
All network + DNS is mocked."""

from __future__ import annotations

import httpx

from jvagent.action.web_fetch.web_fetch_action import WebFetchAction


class _Resp:
    def __init__(self, status=200, headers=None, content=b"", encoding="utf-8"):
        self.status_code = status
        self.headers = headers or {}
        self.content = content
        self.encoding = encoding


def _install_client(monkeypatch, responses):
    class _Client:
        def __init__(self, *a, **k):
            self._responses = list(responses)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, headers=None):
            return self._responses.pop(0)

    monkeypatch.setattr(httpx, "AsyncClient", _Client)


def _allow_all_hosts(monkeypatch, blocked=("127.0.0.1", "localhost")):
    async def _ok(self, host):
        return host not in blocked

    monkeypatch.setattr(WebFetchAction, "_host_allowed", _ok)


# --- SSRF / scheme validation (no network) --------------------------------


async def test_rejects_non_http_scheme():
    out = await WebFetchAction().fetch("file:///etc/passwd")
    assert "only http/https" in out


async def test_host_allowed_blocks_private_ip_literals():
    # IP literals resolve locally — no DNS / network.
    a = WebFetchAction()
    assert await a._host_allowed("127.0.0.1") is False  # loopback
    assert await a._host_allowed("10.0.0.5") is False  # private
    assert await a._host_allowed("192.168.1.1") is False  # private
    assert await a._host_allowed("169.254.169.254") is False  # link-local metadata
    assert await a._host_allowed("8.8.8.8") is True  # public


async def test_allow_private_hosts_opt_in():
    a = WebFetchAction()
    a.allow_private_hosts = True
    assert await a._host_allowed("127.0.0.1") is True


async def test_fetch_refuses_private_host_end_to_end():
    out = await WebFetchAction().fetch("http://127.0.0.1:8000/admin")
    assert "not permitted" in out


# --- Fetch + extraction (mocked client) -----------------------------------


async def test_html_extracted_to_markdown(monkeypatch):
    _allow_all_hosts(monkeypatch)
    html = (
        b"<html><head><title>My Page</title></head><body>"
        b"<nav>menu menu menu</nav>"
        b"<main><h1>Heading</h1><p>Hello <b>world</b>.</p></main>"
        b"<script>evil()</script><footer>foot</footer></body></html>"
    )
    _install_client(
        monkeypatch,
        [_Resp(headers={"content-type": "text/html; charset=utf-8"}, content=html)],
    )
    out = await WebFetchAction().fetch("https://example.com/post")
    assert "# Source: https://example.com/post" in out
    assert "Title: My Page" in out
    assert "UNTRUSTED WEB CONTENT" in out
    assert "Heading" in out and "Hello" in out
    assert "evil()" not in out  # script stripped
    assert "menu menu" not in out  # nav stripped
    assert "foot" not in out  # footer stripped


async def test_truncation_applies(monkeypatch):
    _allow_all_hosts(monkeypatch)
    big = "<html><body><main>" + ("x " * 5000) + "</main></body></html>"
    _install_client(
        monkeypatch,
        [_Resp(headers={"content-type": "text/html"}, content=big.encode())],
    )
    out = await WebFetchAction().fetch("https://example.com", max_chars=500)
    assert "[truncated at 500 chars]" in out


async def test_unsupported_content_type_rejected(monkeypatch):
    _allow_all_hosts(monkeypatch)
    _install_client(
        monkeypatch,
        [_Resp(headers={"content-type": "application/pdf"}, content=b"%PDF")],
    )
    out = await WebFetchAction().fetch("https://example.com/file.pdf")
    assert "unsupported content type: application/pdf" in out


async def test_plain_text_passed_through(monkeypatch):
    _allow_all_hosts(monkeypatch)
    _install_client(
        monkeypatch,
        [_Resp(headers={"content-type": "text/plain"}, content=b"just some text")],
    )
    out = await WebFetchAction().fetch("https://example.com/robots.txt")
    assert "just some text" in out


async def test_redirect_to_private_host_blocked(monkeypatch):
    # First hop public (allowed), redirects to a loopback host → blocked on re-validate.
    _allow_all_hosts(monkeypatch)
    _install_client(
        monkeypatch,
        [_Resp(status=302, headers={"location": "http://127.0.0.1/secret"})],
    )
    out = await WebFetchAction().fetch("https://example.com/redir")
    assert "not permitted" in out


async def test_too_many_redirects(monkeypatch):
    _allow_all_hosts(monkeypatch)
    loop_resp = _Resp(status=302, headers={"location": "https://example.com/next"})
    _install_client(monkeypatch, [loop_resp] * 10)
    a = WebFetchAction()
    a.max_redirects = 2
    out = await a.fetch("https://example.com/start")
    assert "too many redirects" in out


# --- Tool surface ----------------------------------------------------------


async def test_get_tools_shape():
    tools = await WebFetchAction().get_tools()
    assert [t.name for t in tools] == ["web_fetch__fetch"]
    schema = tools[0].parameters_schema
    assert schema["required"] == ["url"]
