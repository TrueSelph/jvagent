"""VisionAction (ADR-0021): canonical image extraction + suppression, the
dedicated-model describe() pass, and the interpret_images tool."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from jvagent.action.vision.vision_action import (
    IMAGE_INTERPRETATION_PROMPT,
    VisionAction,
    image_urls_from_visitor,
)


def _visitor(data):
    return SimpleNamespace(data=data)


async def test_image_urls_suppression():
    assert (
        image_urls_from_visitor(
            _visitor({"image_urls": ["u"], "image_interpretation": False})
        )
        == []
    )
    assert image_urls_from_visitor(_visitor({"image_urls": ["u"]})) == ["u"]
    assert image_urls_from_visitor(_visitor({})) == []


async def test_describe_runs_dedicated_model(monkeypatch):
    va = VisionAction()
    model = MagicMock()
    model.create_multimodal_content = MagicMock(return_value=[{"type": "text"}])
    model.generate = AsyncMock(return_value="a red car")

    async def _ma(self, required=False):
        return model

    monkeypatch.setattr(VisionAction, "get_model_action", _ma)
    out = await va.describe(_visitor({"image_urls": ["http://x/img.png"]}))
    assert out == "a red car"
    assert model.create_multimodal_content.called


async def test_describe_prompt_precedence(monkeypatch):
    """Per-call prompt > interpretation_prompt attr > IMAGE_INTERPRETATION_PROMPT."""
    model = MagicMock()
    model.create_multimodal_content = MagicMock(return_value=[{"type": "text"}])
    model.generate = AsyncMock(return_value="ok")

    async def _ma(self, required=False):
        return model

    monkeypatch.setattr(VisionAction, "get_model_action", _ma)
    visitor = _visitor({"image_urls": ["http://x/img.png"]})

    # Default: the canonical constant.
    va = VisionAction()
    await va.describe(visitor)
    assert model.create_multimodal_content.call_args.kwargs["text"] == (
        IMAGE_INTERPRETATION_PROMPT
    )

    # agent.yaml override via the attribute.
    va.interpretation_prompt = "Only read the text."
    await va.describe(visitor)
    assert model.create_multimodal_content.call_args.kwargs["text"] == (
        "Only read the text."
    )

    # Per-call prompt wins over the attribute.
    await va.describe(visitor, prompt="Just colors.")
    assert model.create_multimodal_content.call_args.kwargs["text"] == "Just colors."


async def test_describe_no_images_is_inert(monkeypatch):
    va = VisionAction()
    called = {"n": 0}

    async def _ma(self, required=False):
        called["n"] += 1
        return MagicMock()

    monkeypatch.setattr(VisionAction, "get_model_action", _ma)
    assert await va.describe(_visitor({})) == ""
    assert called["n"] == 0  # no model resolution when there are no images


async def test_interpret_images_tool(monkeypatch):
    va = VisionAction()

    async def _desc(self, visitor=None, images=None, **kwargs):
        return "desc" if image_urls_from_visitor(visitor) else ""

    monkeypatch.setattr(VisionAction, "describe", _desc)
    tools = await va.get_tools()
    assert [t.name for t in tools] == ["interpret_images"]
    res = await tools[0].execute(visitor=_visitor({"image_urls": ["u"]}))
    assert "desc" in str(res.content)
    res2 = await tools[0].execute(visitor=_visitor({}))
    assert "no images" in str(res2.content)
