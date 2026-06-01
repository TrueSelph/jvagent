"""Orchestrator upload ingestion (ADR-0021 S4): every uploaded file in
visitor.data becomes a source="upload" artifact — bytes persisted to per-user
storage (path on the artifact, not inline), text decoded into the payload,
binaries referenced by path. Dedup + gating."""

from __future__ import annotations

import base64
from types import SimpleNamespace

import pytest

from jvagent.action.orchestrator.orchestrator_interact_action import (
    OrchestratorInteractAction,
)

pytestmark = pytest.mark.asyncio


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


class _Conv:
    def __init__(self):
        self.added = []

    async def add_artifact(self, interaction=None, **kw):
        self.added.append(kw)
        return SimpleNamespace(**kw)


class _App:
    def __init__(self):
        self.saved = []

    async def save_file(self, path, content, metadata=None):
        self.saved.append({"path": path, "size": len(content), "metadata": metadata})
        return True


def _patch(monkeypatch, app):
    async def _get():
        return app

    monkeypatch.setattr("jvagent.core.app.App.get", staticmethod(_get))

    async def _resolve_agent_user(visitor):
        return ("ag", "us")

    monkeypatch.setattr("jvagent.core.sandbox.resolve_agent_user", _resolve_agent_user)


def _visitor(data, conv):
    return SimpleNamespace(
        data=data, conversation=conv, interaction=SimpleNamespace(id="int1")
    )


async def test_ingest_writes_artifact_per_file(monkeypatch):
    app = _App()
    _patch(monkeypatch, app)
    ex = OrchestratorInteractAction()
    conv = _Conv()
    data = {
        "image_urls": [
            {
                "base64": _b64(b"\x89PNGdata"),
                "mime_type": "image/png",
                "filename": "a.png",
            }
        ],
        "whatsapp_media": [
            {
                "base64": _b64(b"%PDF-1.4 ..."),
                "mime_type": "application/pdf",
                "filename": "r.pdf",
            }
        ],
        "documents": [
            {
                "base64": _b64(b"col1,col2\n1,2"),
                "mime_type": "text/csv",
                "filename": "d.csv",
            }
        ],
    }
    n = await ex._ingest_uploads(_visitor(data, conv))
    assert n == 3
    by_name = {a["filename"]: a for a in conv.added}

    # all are source="upload" with file metadata + a stored path
    assert {a["source"] for a in conv.added} == {"upload"}
    assert by_name["a.png"]["kind"] == "image"
    assert by_name["r.pdf"]["kind"] == "file"
    assert by_name["d.csv"]["kind"] == "text"

    # text file → decoded content is the payload (queryable)
    assert by_name["d.csv"]["data"] == "col1,col2\n1,2"
    # binary → descriptor payload referencing the stored path, not the bytes
    assert "Stored at:" in by_name["r.pdf"]["data"]
    for nm in ("a.png", "r.pdf", "d.csv"):
        assert by_name[nm]["path"] and by_name[nm]["mime"]
        assert by_name[nm]["size"] > 0
    # bytes persisted once per file (lean graph: path, not blob)
    assert len(app.saved) == 3
    assert all(
        s["path"].startswith("ag/") or "/uploads/" in s["path"] for s in app.saved
    )


async def test_ingest_dedups_within_turn(monkeypatch):
    app = _App()
    _patch(monkeypatch, app)
    ex = OrchestratorInteractAction()
    conv = _Conv()
    entry = {"base64": _b64(b"same"), "mime_type": "image/png", "filename": "dup.png"}
    data = {"image_urls": [entry, dict(entry)]}
    n = await ex._ingest_uploads(_visitor(data, conv))
    assert n == 1 and len(conv.added) == 1


async def test_ingest_gated_off(monkeypatch):
    app = _App()
    _patch(monkeypatch, app)
    ex = OrchestratorInteractAction()
    ex.ingest_uploads = False
    conv = _Conv()
    data = {
        "image_urls": [
            {"base64": _b64(b"x"), "mime_type": "image/png", "filename": "a.png"}
        ]
    }
    assert await ex._ingest_uploads(_visitor(data, conv)) == 0
    assert conv.added == [] and app.saved == []


async def test_ingest_no_uploads_is_inert(monkeypatch):
    app = _App()
    _patch(monkeypatch, app)
    ex = OrchestratorInteractAction()
    conv = _Conv()
    assert await ex._ingest_uploads(_visitor({}, conv)) == 0
    assert conv.added == []
