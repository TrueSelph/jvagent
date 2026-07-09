"""``pageindex__assimilate`` resolves a path-like ``doc`` from the per-user
sandbox and ingests the file CONTENT — never the literal filename string.

Regression: the model wrote a report to a sandbox file via ``code_execution__bash``
then passed the filename as ``doc``; assimilate ingested ``"<filename>"`` as the
document body and the real content was lost. The fix resolves the sandbox file
(same slice file_interface/code_execution use) and fails loud when a path-like
string can't be read, instead of silently ingesting it.
"""

from __future__ import annotations

import json

import pytest

from jvagent.action.pageindex.documents import (
    assimilate_document,
    looks_like_doc_path,
)
from jvagent.action.pageindex.pageindex_action.pageindex_action import (
    PageIndexAction,
)

pytestmark = pytest.mark.asyncio


# --- the path-vs-content heuristic -----------------------------------------


def test_looks_like_doc_path_classifies():
    assert looks_like_doc_path("trending_ai_github_projects.md")
    assert looks_like_doc_path("output/report.pdf")
    assert looks_like_doc_path("notes.txt")
    assert looks_like_doc_path("my report.md")  # spaces before a clean ext
    # Content, not a path:
    assert not looks_like_doc_path("# Report\n\nThe actual content here.")
    assert not looks_like_doc_path("https://example.com/x.pdf")
    assert not looks_like_doc_path("A single line of prose with no extension")
    assert not looks_like_doc_path("See report.md for the details")  # trailing words
    assert not looks_like_doc_path("")


# --- documents.py defense-in-depth -----------------------------------------


async def test_assimilate_document_rejects_unresolved_path():
    # A path/filename that doesn't resolve must raise — NOT be ingested as the
    # literal string (the data-loss bug).
    with pytest.raises(ValueError):
        await assimilate_document("trending_ai_github_projects.md", persist=False)


# --- the tool layer: sandbox resolution ------------------------------------


def _patch_sandbox(monkeypatch, *, content=None, exc=None, visitor=object()):
    """Patch the dispatch visitor + sandbox text read used by _resolve_sandbox_doc."""
    monkeypatch.setattr(
        "jvagent.tooling.tool_executor.get_tool_visitor",
        lambda: visitor,
    )

    async def _read_text(_visitor, _path, **_kw):
        if exc is not None:
            raise exc
        return content

    monkeypatch.setattr(
        "jvagent.action.file_interface._core.read_text_file", _read_text
    )


def _capture_assimilate(monkeypatch):
    seen = {}

    async def _fake(self, doc, *, doc_name=None, **kw):
        seen["doc"] = doc
        seen["doc_name"] = doc_name
        return {"ok": True}

    monkeypatch.setattr(PageIndexAction, "assimilate", _fake)
    return seen


async def test_tool_resolves_sandbox_file_to_content(monkeypatch):
    _patch_sandbox(monkeypatch, content="# Trending\n\nThe real report body.")
    seen = _capture_assimilate(monkeypatch)

    inst = PageIndexAction()
    out = json.loads(await inst._t_assimilate(doc="trending_ai_github_projects.md"))

    assert out == {"ok": True}
    # Ingested the FILE CONTENT, not the filename.
    assert seen["doc"] == "# Trending\n\nThe real report body."
    assert seen["doc_name"] == "trending_ai_github_projects.md"


async def test_tool_errors_when_path_unreadable(monkeypatch):
    _patch_sandbox(monkeypatch, exc=FileNotFoundError("nope"))
    seen = _capture_assimilate(monkeypatch)

    inst = PageIndexAction()
    out = json.loads(await inst._t_assimilate(doc="missing.md"))

    assert "error" in out
    assert "doc" not in seen  # assimilate never called — nothing ingested


async def test_tool_errors_without_execution_context(monkeypatch):
    _patch_sandbox(monkeypatch, content="x", visitor=None)
    seen = _capture_assimilate(monkeypatch)

    inst = PageIndexAction()
    out = json.loads(await inst._t_assimilate(doc="report.md"))

    assert "error" in out and "doc" not in seen


async def test_tool_passes_raw_text_through(monkeypatch):
    # Raw content (has newlines) is not path-like → no sandbox read, ingested as-is.
    def _boom(*a, **k):
        raise AssertionError("sandbox read must not run for raw content")

    monkeypatch.setattr("jvagent.action.file_interface._core.read_text_file", _boom)
    seen = _capture_assimilate(monkeypatch)

    inst = PageIndexAction()
    body = "# Title\n\nThis is the document body, passed directly."
    out = json.loads(await inst._t_assimilate(doc=body))

    assert out == {"ok": True}
    assert seen["doc"] == body
