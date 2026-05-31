"""IA-as-tool: InteractAction.get_tools() furnishes the tool (desc + triggers,
forward to execute); the Orchestrator binds the visitor + AC + terminal via
``wrap_action_tool``. Plus PersonaAction reply/respond tools."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from jvagent.action.orchestrator.access import delegate_resource_label
from jvagent.action.orchestrator.orchestrator_interact_action import (
    OrchestratorInteractAction,
)
from jvagent.action.orchestrator.tools import wrap_action_tool
from jvagent.tooling.tool import Tool
from jvagent.tooling.tool_result import ToolResult

pytestmark = pytest.mark.asyncio


def _visitor():
    v = MagicMock()
    v.interaction = MagicMock()
    v.interaction.response = ""
    return v


async def _persona_tools(persona, visitor):
    """Mirror the orchestrator's responder binding (visitor-bound)."""
    tools = await persona.get_tools()
    return {t.name: wrap_action_tool(t, visitor=visitor) for t in tools}


# --- InteractAction.get_tools() (the tool definition lives on the IA) ---


async def test_interactaction_get_tools_furnishes_anchored_forwarding_tool(monkeypatch):
    from jvagent.action.interview.interview_interact_action import (
        InterviewInteractAction,
    )

    iv = InterviewInteractAction()
    iv.anchors = ["sign up for training", "register for training"]
    iv.description = "Signup interview."

    ran = {"n": 0}

    async def _exec(self, visitor):
        ran["n"] += 1

    monkeypatch.setattr(InterviewInteractAction, "execute", _exec)

    tools = await iv.get_tools()
    assert len(tools) == 1
    tool = tools[0]
    assert tool.name == iv.get_class_name()
    # description carries the action description + its anchors (intent routing).
    assert "Signup interview." in tool.description
    assert "sign up for training" in tool.description

    # Calling the tool forwards to execute() with the supplied visitor.
    result = await tool.call(visitor=_visitor())
    assert ran["n"] == 1
    assert isinstance(result, ToolResult)


async def test_ia_tool_records_action_execution(monkeypatch):
    """An IA reached via its tool registers itself in the interaction's
    executed-action log (it would otherwise be missing — the walker only records
    actions it visits directly)."""
    from jvagent.action.interview.interview_interact_action import (
        InterviewInteractAction,
    )

    iv = InterviewInteractAction()
    iv.anchors = ["sign up for training"]

    async def _exec(self, visitor):
        pass

    monkeypatch.setattr(InterviewInteractAction, "execute", _exec)

    v = _visitor()
    v.record_action_execution = AsyncMock()
    tools = await iv.get_tools()
    await tools[0].call(visitor=v)

    v.record_action_execution.assert_awaited_once_with(iv.get_class_name())


async def test_get_tools_routes_on_manifest_not_bloated_anchors(monkeypatch):
    """Issue #1: routing metadata is the manifest (purpose + activates_on), not
    the runtime-merged anchor catalog — so continuation intents (cancel/confirm/
    update/skip/decline) don't bloat the description or over-match routing."""
    from jvagent.action.interview.interview_interact_action import (
        InterviewInteractAction,
    )

    iv = InterviewInteractAction()
    # Simulate the runtime-merged (bloated) anchor catalog on the instance.
    iv.anchors = [
        "sign up for training",
        "IF entry is listed under ACTIVE TASKS AND the user requests to cancel",
        "User confirms the interview",
        "User skips a question",
    ]
    # Manifest furnishes the clean entry triggers + purpose.
    iv.metadata = {
        "manifest": {
            "purpose": "Run the signup interview for jvagent training.",
            "activates_on": ["user wants to sign up or register for training"],
        }
    }

    async def _exec(self, visitor):
        pass

    monkeypatch.setattr(InterviewInteractAction, "execute", _exec)

    assert iv.routing_triggers() == ["user wants to sign up or register for training"]

    tools = await iv.get_tools()
    desc = tools[0].description
    assert "Run the signup interview" in desc  # manifest purpose
    assert "sign up or register for training" in desc  # manifest activates_on
    # Continuation intents are NOT in the routing description.
    assert "cancel" not in desc.lower()
    assert "confirms" not in desc.lower()
    assert "skips" not in desc.lower()


async def test_interactaction_get_tools_empty_without_anchors(monkeypatch):
    from jvagent.action.interview.interview_interact_action import (
        InterviewInteractAction,
    )

    iv = InterviewInteractAction()
    iv.anchors = []

    async def _no_dynamic(self, conversation=None):
        return None

    monkeypatch.setattr(InterviewInteractAction, "get_anchors", _no_dynamic)
    assert await iv.get_tools() == []  # not model-routable without anchors


# --- wrap_action_tool: orchestrator binds visitor + AC + terminal on the IA tool ---


def _fake_tool(name="SignupIA", desc="Signup. Use when...", on_call=None):
    async def _call(**kwargs):
        if on_call:
            on_call(kwargs)
        return ToolResult(content="(ran)")

    return Tool(name=name, description=desc, execute=lambda **k: _call(**k))


async def test_wrap_ia_tool_injects_visitor_and_is_terminal():
    seen = {}
    v = _visitor()
    tool = _fake_tool(on_call=lambda kw: seen.update(kw))
    sk = wrap_action_tool(tool, visitor=v, terminal=True)
    assert sk.name == "SignupIA" and sk.terminal is True
    out = await sk.run({})
    assert seen.get("visitor") is v  # visitor patched through to the IA tool
    assert out == "(ran)"


async def test_wrap_ia_tool_access_denied():
    ac = MagicMock()
    ac.policy_applies = MagicMock(return_value=True)
    ac.has_action_access = AsyncMock(return_value=False)
    agent = MagicMock()
    agent.get_access_control_action = AsyncMock(return_value=ac)

    ran = {"n": 0}
    tool = _fake_tool(on_call=lambda kw: ran.__setitem__("n", ran["n"] + 1))
    sk = wrap_action_tool(
        tool,
        visitor=_visitor(),
        agent=agent,
        user_id="u",
        channel="web",
        access_label=delegate_resource_label("SignupIA"),
    )
    out = await sk.run({})
    assert out == "(access denied)" and ran["n"] == 0


async def test_persona_tools_reply_publishes_thin():
    from jvagent.action.persona.persona_action import PersonaAction

    persona = PersonaAction()
    piped = []

    async def _pipe(text, interaction, visitor, streaming=False, transient=False):
        piped.append(text)

    persona._pipe_response = _pipe  # type: ignore[assignment]

    v = _visitor()
    tools = await _persona_tools(persona, v)
    assert set(tools) == {"reply", "respond"}

    out = await tools["reply"].run({"text": "hi there"})
    assert piped == ["hi there"]
    assert "replied" in out


async def test_persona_tools_respond_frames_via_respond(monkeypatch):
    from jvagent.action.persona.persona_action import PersonaAction

    persona = PersonaAction()
    captured = {}

    async def _respond(self, interaction, visitor=None, **kw):
        captured["called"] = True
        return "styled reply"

    monkeypatch.setattr(PersonaAction, "respond", _respond)

    v = _visitor()
    v.add_directives = AsyncMock()
    tools = await _persona_tools(persona, v)

    out = await tools["respond"].run({"text": "the order shipped"})
    v.add_directives.assert_awaited_once()
    assert captured.get("called") is True
    assert "responded" in out


# --- get_responder() resolution + SE egress wiring (ADR-0014) ---


async def test_get_responder_prefers_reply_action(monkeypatch):
    from jvagent.action.persona.persona_action import PersonaAction
    from jvagent.action.reply.reply_action import ReplyAction

    reply = ReplyAction()
    persona = PersonaAction()
    reg = {"ReplyAction": reply, "PersonaAction": persona}

    async def _ga(self, name):
        key = name if isinstance(name, str) else getattr(name, "__name__", str(name))
        return reg.get(key)

    monkeypatch.setattr(OrchestratorInteractAction, "get_action", _ga)
    ex = OrchestratorInteractAction()

    assert (await ex.get_responder()) is reply  # ReplyAction preferred
    reg.pop("ReplyAction")
    assert (await ex.get_responder()) is persona  # falls back to PersonaAction


async def test_orchestrator_emits_through_reply_action(
    make_orchestrator, make_visitor, monkeypatch
):
    """The SE picks up ReplyAction from enabled actions and routes reply through
    it (egress congruence, ADR-0014)."""
    from jvagent.action.reply.reply_action import ReplyAction

    reply = ReplyAction()

    async def _pipe(
        self, content, interaction, visitor, streaming=False, transient=False
    ):
        visitor.interaction.response = (visitor.interaction.response or "") + content
        return True

    monkeypatch.setattr(ReplyAction, "_pipe_response", _pipe)

    ex = make_orchestrator(
        actions=[reply],
        decisions=[{"action": "reply", "args": {"text": "Hi from ReplyAction"}}],
    )
    v = make_visitor(utterance="hello")
    await ex.execute(v)
    assert v.interaction.response == "Hi from ReplyAction"


async def test_orchestrator_reply_applies_channel_format(
    make_orchestrator, make_visitor, monkeypatch
):
    """Full SE path: reply on a non-default channel (sms) composes via respond
    and injects the channel format into the model call (ADR-0014)."""
    from types import SimpleNamespace

    from jvagent.action.reply.reply_action import ReplyAction

    reply = ReplyAction()
    model = MagicMock()
    model.generate = AsyncMock(return_value="short plain reply")

    async def _ma(self, required=False):
        return model

    async def _agent(self):
        return SimpleNamespace(alias="Ex", role="a guide")

    async def _pipe(
        self, content, interaction, visitor, streaming=False, transient=False
    ):
        visitor.interaction.response = content
        return True

    monkeypatch.setattr(ReplyAction, "get_model_action", _ma)
    monkeypatch.setattr(ReplyAction, "get_agent", _agent)
    monkeypatch.setattr(ReplyAction, "_pipe_response", _pipe)

    ex = make_orchestrator(
        actions=[reply],
        decisions=[{"action": "reply", "args": {"text": "Here is a **bold** answer."}}],
    )
    v = make_visitor(utterance="hi", channel="sms")
    await ex.execute(v)

    sysprompt = model.generate.call_args.kwargs["system"]
    assert "CHANNEL FORMATTING" in sysprompt  # channel reached the responder
    assert "Plain text only" in sysprompt  # the sms directive was applied
    assert v.interaction.response == "short plain reply"
