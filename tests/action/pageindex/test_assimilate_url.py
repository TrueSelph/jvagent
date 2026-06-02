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
from jvagent.action.pageindex.url_guard import require_path_under_work_dir

pytestmark = pytest.mark.asyncio


async def _patch_fetch(monkeypatch, *, content: bytes, content_type: str):
    async def _fake(url: str, **kwargs):
        return content, "download", content_type

    monkeypatch.setattr(
        "jvagent.action.pageindex.url_guard.fetch_url_bytes_capped",
        _fake,
    )


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
    await _patch_fetch(
        monkeypatch,
        content=b"%PDF-1.7 ...",
        content_type="application/pdf",
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
    await _patch_fetch(monkeypatch, content=html, content_type="text/html")
    path, _ = await _download_url_to_workdir(
        "https://example.com/page", None, str(tmp_path)
    )
    assert path.endswith(".md")
    text = Path(path).read_text(encoding="utf-8")
    assert "Title" in text and "Body text." in text
    assert "x()" not in text


async def test_assimilate_tool_coalesces_content_alias(monkeypatch):
    """The model sometimes passes `content=`/`text=` instead of `doc=`; the tool
    must coalesce rather than raise 'unexpected keyword argument'."""
    from jvagent.action.pageindex.pageindex_action.pageindex_action import (
        PageIndexAction,
    )

    seen = {}

    async def _fake_assimilate(self, doc, *, doc_name=None, **kw):
        seen["doc"] = doc
        seen["doc_name"] = doc_name
        return {"ok": True}

    monkeypatch.setattr(PageIndexAction, "assimilate", _fake_assimilate)
    inst = PageIndexAction()
    tools = {t.name: t for t in await inst.get_tools()}
    assim = tools["pageindex__assimilate"]

    out = await assim.execute(content="hello world", name="Greeting")
    assert seen["doc"] == "hello world"
    assert seen["doc_name"] == "Greeting"
    assert '"ok": true' in out

    # The exact alias the model used in the field ('source').
    seen.clear()
    await assim.execute(source="https://example.com/x.pdf")
    assert seen["doc"] == "https://example.com/x.pdf"

    # Any stray string kwarg is treated as the document (only doc/doc_name exist).
    seen.clear()
    await assim.execute(whatever_arg="some text")
    assert seen["doc"] == "some text"

    # And the canonical name still works.
    seen.clear()
    await assim.execute(doc="plain")
    assert seen["doc"] == "plain"

    # Missing entirely → actionable error, not a crash.
    err = await assim.execute()
    assert "no document provided" in err


async def test_download_rejects_non_http_scheme(tmp_path):
    with pytest.raises(ValueError, match="unsupported URL scheme"):
        await _download_url_to_workdir("ftp://example.com/x", None, str(tmp_path))


async def test_download_rejects_private_url(tmp_path):
    with pytest.raises(ValueError, match="could not download URL"):
        await _download_url_to_workdir("http://127.0.0.1/secret", None, str(tmp_path))


async def test_download_network_error_raises_actionable(monkeypatch, tmp_path):
    async def _boom(url: str, **kwargs):
        raise httpx.ConnectError("boom")

    monkeypatch.setattr(
        "jvagent.action.pageindex.url_guard.fetch_url_bytes_capped",
        _boom,
    )
    with pytest.raises(ValueError, match="could not download URL"):
        await _download_url_to_workdir("https://example.com/x", None, str(tmp_path))


def test_require_path_under_work_dir_rejects_outside(tmp_path):
    outside = Path("/etc/passwd")
    if not outside.is_file():
        pytest.skip("no /etc/passwd on this host")
    with pytest.raises(ValueError, match="work directory"):
        require_path_under_work_dir(str(outside), str(tmp_path))
