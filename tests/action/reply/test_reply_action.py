"""ReplyAction (ADR-0014) — the lean egress voice: reply (thin publish), respond
(identity-voiced single model call), publish, and the reply/respond tools.
Identity is read from the Agent node; shaping is optional."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from jvagent.action.reply.reply_action import ReplyAction

pytestmark = pytest.mark.asyncio


def _visitor_no_bus():
    inter = MagicMock()
    inter.response = ""

    def _set(x):
        inter.response = x
        return True

    inter.set_response = _set
    inter.save = AsyncMock()
    v = MagicMock()
    v.interaction = inter
    v.response_bus = None
    v.session_id = None
    v.stream = False
    return v


def _patch_agent(monkeypatch, alias="Ada", role="a helpful guide"):
    async def _agent(self):
        return SimpleNamespace(alias=alias, role=role)

    monkeypatch.setattr(ReplyAction, "get_agent", _agent)


def _visitor_with(directives=None, parameters=None):
    v = _visitor_no_bus()
    v.interaction.get_unexecuted_directives = MagicMock(return_value=directives or [])
    v.interaction.parameters = parameters or []
    return v


async def test_reply_publishes_literal_no_bus():
    ra = ReplyAction()
    v = _visitor_no_bus()
    assert await ra.reply("hi there", v) is True
    assert v.interaction.response == "hi there"


async def test_reply_is_slim_with_no_shaping(monkeypatch):
    """No directives/parameters → thin publish, no model call."""
    ra = ReplyAction()
    _patch_agent(monkeypatch)
    called = {"n": 0}

    async def _ma(self, required=False):
        called["n"] += 1
        return MagicMock(generate=AsyncMock(return_value="x"))

    monkeypatch.setattr(ReplyAction, "get_model_action", _ma)
    v = _visitor_with()  # no directives, no parameters
    assert await ra.reply("plain answer", v) is True
    assert v.interaction.response == "plain answer"
    assert called["n"] == 0  # slim: model never invoked


async def test_reply_applies_directives(monkeypatch):
    ra = ReplyAction()
    _patch_agent(monkeypatch)
    model = MagicMock()
    model.generate = AsyncMock(return_value="Composed with directive.")

    async def _ma(self, required=False):
        return model

    monkeypatch.setattr(ReplyAction, "get_model_action", _ma)
    v = _visitor_with(directives=[{"content": "Mention the welcome offer."}])
    assert await ra.reply("Here's your answer.", v) is True
    assert v.interaction.response == "Composed with directive."
    # The message is folded in as the lead directive and composed WITH the
    # pending directive (so the directive can't override the reply's substance).
    prompt = model.generate.call_args.kwargs["prompt"]
    assert "Here's your answer." in prompt and "welcome offer" in prompt


async def test_reply_directive_does_not_override_message(monkeypatch):
    """A queued directive (e.g. a first-contact intro) must not replace the
    model's substantive reply — both are composed together."""
    ra = ReplyAction()
    _patch_agent(monkeypatch)
    model = MagicMock()
    model.generate = AsyncMock(return_value="Report saved at notes.md. I'm Ada.")

    async def _ma(self, required=False):
        return model

    monkeypatch.setattr(ReplyAction, "get_model_action", _ma)
    v = _visitor_with(directives=[{"content": "Introduce yourself by name."}])
    assert await ra.reply("Your report is saved at notes.md.", v) is True
    prompt = model.generate.call_args.kwargs["prompt"]
    # Both the task message and the intro directive reach the model together.
    assert "report is saved at notes.md" in prompt
    assert "Introduce yourself" in prompt


async def test_reply_applies_parameters(monkeypatch):
    ra = ReplyAction()
    _patch_agent(monkeypatch)
    model = MagicMock()
    model.generate = AsyncMock(return_value="Composed.")

    async def _ma(self, required=False):
        return model

    monkeypatch.setattr(ReplyAction, "get_model_action", _ma)
    v = _visitor_with(
        parameters=[{"condition": "user asks about price", "response": "quote $9"}]
    )
    assert await ra.reply("ok", v) is True
    sysprompt = model.generate.call_args.kwargs["system"]
    assert "CONDITIONAL RULES" in sysprompt and "user asks about price" in sysprompt


async def test_collect_parameters():
    inter = MagicMock()
    inter.parameters = [{"condition": "X", "response": "Y"}, {"response": "Z"}]
    out = ReplyAction._collect_parameters(None, inter)
    assert "- When X: Y" in out and "- Z" in out


async def test_get_channel_format_default_is_slim_and_override():
    ra = ReplyAction()
    assert ra.get_channel_format("web") == ""  # default channel → slim
    assert ra.get_channel_format("default") == ""
    assert "Plain" in ra.get_channel_format("sms")  # built-in
    ra.channel_formats = {"default": "Custom web rule."}
    assert ra.get_channel_format("web") == "Custom web rule."  # descriptor override


async def test_reply_applies_channel_format_on_special_channel(monkeypatch):
    """A channel that needs formatting (sms) makes reply compose via respond and
    inject the channel format — even with no directives/parameters."""
    ra = ReplyAction()
    _patch_agent(monkeypatch)
    model = MagicMock()
    model.generate = AsyncMock(return_value="short plain reply")

    async def _ma(self, required=False):
        return model

    monkeypatch.setattr(ReplyAction, "get_model_action", _ma)
    v = _visitor_with()  # no directives/params
    v.channel = "sms"
    assert await ra.reply("Here is a **markdown** answer.", v) is True
    sysprompt = model.generate.call_args.kwargs["system"]
    assert "CHANNEL FORMATTING" in sysprompt and "Plain text only" in sysprompt


async def test_reply_default_channel_stays_slim(monkeypatch):
    """Default/web channel with no shaping → thin publish, no model call."""
    ra = ReplyAction()
    _patch_agent(monkeypatch)
    called = {"n": 0}

    async def _ma(self, required=False):
        called["n"] += 1
        return MagicMock(generate=AsyncMock(return_value="x"))

    monkeypatch.setattr(ReplyAction, "get_model_action", _ma)
    v = _visitor_with()
    v.channel = "web"
    assert await ra.reply("plain web answer", v) is True
    assert v.interaction.response == "plain web answer"
    assert called["n"] == 0  # slim — channel format absent for web


async def test_reply_empty_is_noop():
    ra = ReplyAction()
    v = _visitor_no_bus()
    assert await ra.reply("   ", v) is False
    assert v.interaction.response == ""


async def test_identity_and_system_prompt(monkeypatch):
    ra = ReplyAction()
    _patch_agent(monkeypatch)
    assert await ra._identity() == "You are Ada, a helpful guide."
    sp = await ra._system_prompt()
    assert "You are Ada, a helpful guide." in sp
    assert "invitation closers" in sp  # keeper voice rules baked in


async def test_respond_generates_in_identity_and_publishes(monkeypatch):
    ra = ReplyAction()
    _patch_agent(monkeypatch, role="a guide")
    model = MagicMock()
    model.generate = AsyncMock(return_value="Voiced answer.")

    async def _ma(self, required=False):
        return model

    monkeypatch.setattr(ReplyAction, "get_model_action", _ma)

    v = _visitor_no_bus()
    out = await ra.respond(v.interaction, visitor=v, text="raw answer")

    assert out == "Voiced answer."
    assert v.interaction.response == "Voiced answer."
    kwargs = model.generate.call_args.kwargs
    assert "You are Ada, a guide." in kwargs["system"]  # identity drives the voice
    assert kwargs["prompt"] == "raw answer"


async def test_respond_without_model_thin_publishes(monkeypatch):
    ra = ReplyAction()
    _patch_agent(monkeypatch, alias="", role="")

    async def _ma(self, required=False):
        return None

    monkeypatch.setattr(ReplyAction, "get_model_action", _ma)

    v = _visitor_no_bus()
    out = await ra.respond(v.interaction, visitor=v, text="just say this")
    assert out == "just say this" and v.interaction.response == "just say this"


async def test_collect_directive_text():
    inter = MagicMock()
    inter.get_unexecuted_directives = MagicMock(
        return_value=[{"content": "Tell the user: X"}, {"content": ""}]
    )
    assert ReplyAction._collect_directive_text(None, inter) == "Tell the user: X"
    assert ReplyAction._collect_directive_text(["A", "B"], inter) == "A\nB"


async def test_get_tools_reply_and_respond(monkeypatch):
    ra = ReplyAction()
    _patch_agent(monkeypatch, alias="", role="")
    tools = await ra.get_tools()
    assert sorted(t.name for t in tools) == ["reply", "respond"]

    v = _visitor_no_bus()
    reply_tool = next(t for t in tools if t.name == "reply")
    await reply_tool.call(visitor=v, text="hello")
    assert v.interaction.response == "hello"
