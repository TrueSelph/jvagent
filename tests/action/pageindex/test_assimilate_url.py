"""URL ingestion for pageindex assimilate: the in-process path downloads a URL
(content-type aware) before ingesting, instead of crashing on a non-existent
path. HTML is stripped to text; PDFs keep their bytes. Network is mocked."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from jvagent.action.pageindex.documents import (
    _download_url_to_workdir,
    _html_to_text,
)

pytestmark = pytest.mark.asyncio


class _Resp:
    def __init__(self, content=b"", headers=None, encoding="utf-8"):
        self.content = content
        self.headers = headers or {}
        self.encoding = encoding

    def raise_for_status(self):
        return None


def _install(monkeypatch, resp):
    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, headers=None):
            return resp

    monkeypatch.setattr(httpx, "AsyncClient", _Client)


def test_html_to_text_strips_tags_and_scripts():
    html = (
        "<html><head><title>T</title><style>.x{}</style></head>"
        "<body><script>evil()</script><h1>Heading</h1>"
        "<p>Hello <b>world</b>.</p></body></html>"
    )
    out = _html_to_text(html)
    assert "Heading" in out and "Hello" in out and "world" in out
    assert "evil()" not in out and ".x{}" not in out


async def test_download_pdf_keeps_bytes_and_ext(monkeypatch, tmp_path):
    _install(
        monkeypatch,
        _Resp(content=b"%PDF-1.7 ...", headers={"content-type": "application/pdf"}),
    )
    path, url = await _download_url_to_workdir(
        "https://example.com/report", None, str(tmp_path)
    )
    assert url == "https://example.com/report"
    assert path.endswith(".pdf")
    assert Path(path).read_bytes().startswith(b"%PDF")


async def test_download_html_becomes_stripped_markdown(monkeypatch, tmp_path):
    html = (
        b"<html><body><script>x()</script><h1>Title</h1><p>Body text.</p></body></html>"
    )
    _install(
        monkeypatch,
        _Resp(content=html, headers={"content-type": "text/html; charset=utf-8"}),
    )
    path, _ = await _download_url_to_workdir(
        "https://example.com/page", None, str(tmp_path)
    )
    assert path.endswith(".md")
    text = Path(path).read_text(encoding="utf-8")
    assert "Title" in text and "Body text." in text
    assert "x()" not in text  # script stripped


async def test_download_rejects_non_http_scheme(tmp_path):
    with pytest.raises(ValueError, match="unsupported URL scheme"):
        await _download_url_to_workdir("ftp://example.com/x", None, str(tmp_path))


async def test_download_network_error_raises_actionable(monkeypatch, tmp_path):
    class _BoomClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, headers=None):
            raise httpx.ConnectError("boom")

    monkeypatch.setattr(httpx, "AsyncClient", _BoomClient)
    with pytest.raises(ValueError, match="could not download URL"):
        await _download_url_to_workdir("https://example.com/x", None, str(tmp_path))
