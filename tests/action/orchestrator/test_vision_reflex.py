"""Orchestrator pre-loop vision reflex (ADR-0021): gated off = inert; on with
images = VisionAction runs, a source:"vision" artifact is written, and the
interpretation text is returned to seed the loop. Suppression + no-image skip."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from jvagent.action.orchestrator.orchestrator_interact_action import (
    OrchestratorInteractAction,
)


class _Conv:
    def __init__(self):
        self.added = []

    async def add_artifact(self, interaction, **kw):
        self.added.append(kw)
        return SimpleNamespace(**kw)


def _visitor(data, conv=None):
    return SimpleNamespace(
        data=data, conversation=conv, interaction=SimpleNamespace(id="int_1")
    )


async def test_reflex_off_is_inert():
    ex = OrchestratorInteractAction()  # vision defaults False
    conv = _Conv()
    assert await ex._vision_reflex(_visitor({"image_urls": ["u"]}, conv)) == ""
    assert conv.added == []


async def test_reflex_on_writes_vision_artifact(monkeypatch):
    ex = OrchestratorInteractAction()
    ex.vision = True
    fake_vision = SimpleNamespace(describe=AsyncMock(return_value="a red car\nmore"))

    async def _resolve(self, name):
        return fake_vision if name == "VisionAction" else None

    monkeypatch.setattr(OrchestratorInteractAction, "_resolve_action", _resolve)
    conv = _Conv()
    out = await ex._vision_reflex(_visitor({"image_urls": ["u"]}, conv))
    assert out == "a red car\nmore"
    assert conv.added and conv.added[0]["source"] == "vision"
    assert conv.added[0]["summary"] == "a red car"
    assert conv.added[0]["tags"] == ["image", "vision"]
    fake_vision.describe.assert_awaited_once()


async def test_reflex_suppressed():
    ex = OrchestratorInteractAction()
    ex.vision = True
    conv = _Conv()
    data = {"image_urls": ["u"], "image_interpretation": False}
    assert await ex._vision_reflex(_visitor(data, conv)) == ""
    assert conv.added == []


async def test_reflex_no_images():
    ex = OrchestratorInteractAction()
    ex.vision = True
    assert await ex._vision_reflex(_visitor({}, _Conv())) == ""
