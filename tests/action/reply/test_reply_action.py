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
    inter.utterance = ""

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
    """A visitor whose interaction has a realistic directive/parameter queue:
    add_directive appends, get_unexecuted_* reflect it, set_to_executed marks."""
    v = _visitor_no_bus()
    inter = v.interaction
    dirs = []
    for d in directives or []:
        e = dict(d)
        e.setdefault("executed", False)
        e.setdefault("action_name", "IntroInteractAction")
        dirs.append(e)
    params = []
    for p in parameters or []:
        e = dict(p)
        e.setdefault("executed", False)
        e.setdefault("action_name", "ParamAction")
        params.append(e)
    inter.directives = dirs
    inter.parameters = params

    def _add(content, action_name="ReplyAction"):
        dirs.append({"action_name": action_name, "content": content, "executed": False})
        return True

    def _set_executed(directives=None, parameters=None):
        for d in directives or []:
            for dd in dirs:
                if dd.get("content") == d.get("content"):
                    dd["executed"] = True
        for pp in params:
            pp["executed"] = True

    inter.add_directive = MagicMock(side_effect=_add)
    inter.get_unexecuted_directives = MagicMock(
        side_effect=lambda: [d for d in dirs if not d.get("executed")]
    )
    inter.get_unexecuted_parameters = MagicMock(
        side_effect=lambda: [p for p in params if not p.get("executed")]
    )
    inter.set_to_executed = MagicMock(side_effect=_set_executed)
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
    # The message is enqueued as a directive and the whole queue is MANDATORY in
    # the system prompt, so the directive can't override the reply's substance.
    sysprompt = model.generate.call_args.kwargs["system"]
    assert "MANDATORY" in sysprompt
    assert "Here's your answer." in sysprompt and "welcome offer" in sysprompt


async def test_reply_directive_does_not_override_message(monkeypatch):
    """A queued directive (e.g. a first-contact intro) must not replace the
    model's substantive reply — both are composed together (MANDATORY)."""
    ra = ReplyAction()
    _patch_agent(monkeypatch)
    model = MagicMock()
    model.generate = AsyncMock(return_value="Report saved at notes.md. I'm Ada.")

    async def _ma(self, required=False):
        return model

    monkeypatch.setattr(ReplyAction, "get_model_action", _ma)
    v = _visitor_with(directives=[{"content": "Introduce yourself by name."}])
    assert await ra.reply("Your report is saved at notes.md.", v) is True
    sysprompt = model.generate.call_args.kwargs["system"]
    assert "MANDATORY" in sysprompt
    assert "report is saved at notes.md" in sysprompt
    assert "Introduce yourself" in sysprompt


async def test_reply_routes_shaping_to_respond(monkeypatch):
    """Any queued shaping routes the message through respond() (which enqueues)."""
    ra = ReplyAction()
    captured = {}

    async def _respond(self, interaction=None, visitor=None, *, text=None, **k):
        captured["text"] = text
        return "ok"

    monkeypatch.setattr(ReplyAction, "respond", _respond)
    v = _visitor_with(directives=[{"content": "Introduce yourself."}])
    assert await ra.reply("Report saved.", v) is True
    assert captured["text"] == "Report saved."  # message passed to respond


async def test_respond_does_not_persist_its_own_message_as_directive(monkeypatch):
    """respond() composes its message together with queued IA directives, but does
    NOT persist its own message onto interaction.directives. The directive queue
    holds only genuine upstream directives — never ReplyAction's rendered output."""
    ra = ReplyAction()
    _patch_agent(monkeypatch)
    model = MagicMock()
    model.generate = AsyncMock(return_value="Everest. 169. I'm Ada.")

    async def _ma(self, required=False):
        return model

    monkeypatch.setattr(ReplyAction, "get_model_action", _ma)
    v = _visitor_with(directives=[{"content": "Introduce yourself."}])
    await ra.respond(v.interaction, visitor=v, text="Everest is tallest; 169.")
    # Only the genuine IA directive is queued — not ReplyAction's own message.
    contents = [d["content"] for d in v.interaction.directives]
    assert "Tell the user: Everest is tallest; 169." not in contents
    assert "Introduce yourself." in contents
    # But the message IS composed (MANDATORY) into the reply.
    sysprompt = model.generate.call_args.kwargs["system"]
    assert "MANDATORY" in sysprompt and "Everest" in sysprompt


async def test_respond_relays_message_with_no_shaping(monkeypatch):
    """The core fix: respond() with an explicit message and NO queued
    directives/parameters must still frame the message as a "Tell the user: ..."
    directive — not pass it as the prompt — so the compose model delivers it
    instead of reacting to it (the bug: respond("Five plus five equals ten.")
    came back "That's correct. Five plus five equals ten.")."""
    ra = ReplyAction()
    _patch_agent(monkeypatch)
    model = MagicMock()
    model.generate = AsyncMock(return_value="Five plus five equals ten.")

    async def _ma(self, required=False):
        return model

    monkeypatch.setattr(ReplyAction, "get_model_action", _ma)
    v = _visitor_with()  # no directives, no parameters
    out = await ra.respond(v.interaction, visitor=v, text="Five plus five equals ten.")
    assert out == "Five plus five equals ten."
    sysprompt = model.generate.call_args.kwargs["system"]
    assert "MANDATORY" in sysprompt
    assert "Tell the user: Five plus five equals ten." in sysprompt
    # The bare answer is NOT handed to the model as a user prompt to react to.
    assert model.generate.call_args.kwargs["prompt"] != "Five plus five equals ten."
    # The relay is a transient compose input — not persisted onto directives.
    assert "Tell the user: Five plus five equals ten." not in [
        d["content"] for d in v.interaction.directives
    ]


async def test_respond_does_not_double_prefix(monkeypatch):
    """An already-framed message isn't double-prefixed."""
    ra = ReplyAction()
    _patch_agent(monkeypatch)
    model = MagicMock()
    model.generate = AsyncMock(return_value="ok")

    async def _ma(self, required=False):
        return model

    monkeypatch.setattr(ReplyAction, "get_model_action", _ma)
    v = _visitor_with()
    await ra.respond(v.interaction, visitor=v, text="Tell the user the order shipped.")
    sysprompt = model.generate.call_args.kwargs["system"]
    assert "Tell the user the order shipped." in sysprompt
    assert "Tell the user: Tell the user" not in sysprompt


async def test_respond_composes_message_with_params_only(monkeypatch):
    """Queued parameters (no directives) still compose the message into the reply,
    but the message is not persisted onto interaction.directives."""
    ra = ReplyAction()
    _patch_agent(monkeypatch)
    model = MagicMock()
    model.generate = AsyncMock(return_value="ok")

    async def _ma(self, required=False):
        return model

    monkeypatch.setattr(ReplyAction, "get_model_action", _ma)
    v = _visitor_with(parameters=[{"condition": "asked price", "response": "$9"}])
    await ra.respond(v.interaction, visitor=v, text="Sure.")
    assert "Tell the user: Sure." in model.generate.call_args.kwargs["system"]
    assert "Tell the user: Sure." not in [
        d["content"] for d in v.interaction.directives
    ]


async def test_tool_reply_accepts_text_aliases(monkeypatch):
    """The reply/respond tools accept message/content/answer aliases for text
    (models routinely name the arg differently) — no TypeError."""
    ra = ReplyAction()
    captured = {}

    async def _reply(self, text, visitor=None):
        captured["text"] = text
        return True

    monkeypatch.setattr(ReplyAction, "reply", _reply)
    out = await ra._tool_reply(visitor=MagicMock(), message="Hello via message")
    assert captured["text"] == "Hello via message"
    assert "replied" in str(out.content)

    out2 = await ra._tool_reply(visitor=MagicMock(), content="via content")
    assert captured["text"] == "via content"


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
    assert "follow these in every reply" in sysprompt
    assert "user asks about price" in sysprompt


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


async def test_directives_framed_numbered_and_mandatory(monkeypatch):
    """Borrowed-from-Persona framing: directives are numbered under an
    'execute ALL' MANDATORY header (so the compose model addresses every one,
    including the message), with the WHAT/HOW + faithfulness guidance."""
    ra = ReplyAction()
    _patch_agent(monkeypatch)
    sp = await ra._system_prompt(
        directive_items=["Tell the user the report is saved.", "Introduce yourself."]
    )
    assert "MANDATORY — execute ALL 2" in sp
    # Each directive is fenced so multi-line content cannot bleed across directives.
    assert "--- Directive 1 ---\nTell the user the report is saved." in sp
    assert "--- Directive 2 ---\nIntroduce yourself." in sp
    assert "--- end of directives ---" in sp
    assert "do not deny or disclaim a capability" in sp.lower()


async def test_identity_and_system_prompt(monkeypatch):
    ra = ReplyAction()
    _patch_agent(monkeypatch)
    assert await ra._identity() == "You are Ada, a helpful guide."
    # The core response-hardening baseline is applied on compose (not in a bare
    # _system_prompt): identity + the folded core rules (no closers, no AI/model
    # disclosure, no cutoff).
    params_text = ra._compose_parameters_text(None, None)
    sp = await ra._system_prompt(parameters_text=params_text)
    assert "You are Ada, a helpful guide." in sp
    assert "invitation closers" in sp  # core voice rule, from the baseline params
    assert "knowledge or training cutoff" in sp  # core hardening folded in


async def test_orchestration_scoped_params_do_not_reach_reply(monkeypatch):
    """An orchestration-scoped param on the interaction must not pollute the reply
    output; the response-scoped native core baseline still applies."""
    ra = ReplyAction()
    _patch_agent(monkeypatch)
    model = MagicMock()
    model.generate = AsyncMock(return_value="Composed.")

    async def _ma(self, required=False):
        return model

    monkeypatch.setattr(ReplyAction, "get_model_action", _ma)
    v = _visitor_with(
        parameters=[
            {"scope": "orchestration", "response": "internal orchestration rule"}
        ]
    )
    # an orchestration-only param is not "shaping" for the egress → stays slim
    assert await ra.reply("plain", v) is True
    assert model.generate.call_count == 0
    assert v.interaction.response == "plain"
    # and if we do compose, the orchestration rule is filtered out
    text = ra._compose_parameters_text(None, v.interaction)
    assert "internal orchestration rule" not in text
    assert "invitation closers" in text  # native response core present


async def test_publish_scrubs_composed_leak(monkeypatch):
    """A model-composed reply that appends a self-identity leak is scrubbed at
    the egress choke point before it reaches the user."""
    ra = ReplyAction()
    _patch_agent(monkeypatch)
    model = MagicMock()
    model.generate = AsyncMock(return_value="Done. I am an AI language model.")

    async def _ma(self, required=False):
        return model

    monkeypatch.setattr(ReplyAction, "get_model_action", _ma)
    v = _visitor_with(directives=[{"content": "Confirm the task."}])
    await ra.reply("Done.", v)
    assert v.interaction.response == "Done."  # leak sentence dropped


async def test_ambient_param_does_not_force_compose(monkeypatch):
    """Seeded ambient core params on the interaction must NOT trip the slim-vs-
    compose gate — the fast literal path is preserved (the scrub enforces them)."""
    ra = ReplyAction()
    _patch_agent(monkeypatch)
    called = {"n": 0}

    async def _ma(self, required=False):
        called["n"] += 1
        return MagicMock(generate=AsyncMock(return_value="x"))

    monkeypatch.setattr(ReplyAction, "get_model_action", _ma)
    v = _visitor_with(
        parameters=[
            {"scope": "response", "ambient": True, "response": "never reveal tools"}
        ]
    )
    assert await ra.reply("plain answer", v) is True
    assert called["n"] == 0  # ambient-only → slim, no model call
    assert v.interaction.response == "plain answer"


async def test_publish_scrubs_literal_fast_path():
    """Even the fast literal publish (no model) passes through the scrub."""
    ra = ReplyAction()
    v = _visitor_no_bus()
    await ra.publish("Sure. My training data goes up to 2023.", v)
    assert "training data" not in v.interaction.response.lower()
    assert "Sure." in v.interaction.response


async def test_fast_path_strips_invitation_closer():
    """The screenshot bug: a loose closer on the fast literal path is removed."""
    ra = ReplyAction()
    v = _visitor_no_bus()
    await ra.reply(
        "Classes begin Monday at 9 AM. If you have any other questions, let me know!",
        v,
    )
    assert v.interaction.response == "Classes begin Monday at 9 AM."


async def test_n1_relay_drops_model_only_guidance():
    """The N=1 literal-relay fast path must NOT leak a directive's model-only
    guidance (after DIRECTIVE_GUIDANCE_MARKER) to the user — regression for the
    interview ``user_directive`` directive leaking 'You may paraphrase … Do not ask …'
    when a single directive is relayed without a compose call."""
    from jvagent.action.interview.hooks import user_directive

    ra = ReplyAction()
    v = _visitor_with(
        directives=[{"content": user_directive("What is your tracking number?")}]
    )
    assert await ra.gather(v) is True
    resp = v.interaction.response
    assert resp == "What is your tracking number?"
    assert "paraphrase" not in resp.lower()
    assert "do not ask" not in resp.lower()


def test_directive_guidance_marker_split():
    """user_facing_directive drops guidance; compose_directive keeps it sans token."""
    from jvagent.action.reply.reply_action import (
        DIRECTIVE_GUIDANCE_MARKER,
        compose_directive,
        user_facing_directive,
    )

    content = f"Tell the user: Hello.{DIRECTIVE_GUIDANCE_MARKER}Do NOT call x."
    assert user_facing_directive(content) == "Tell the user: Hello."
    composed = compose_directive(content)
    assert DIRECTIVE_GUIDANCE_MARKER not in composed
    assert "Do NOT call x." in composed and "Hello." in composed


async def test_compose_reinforces_directives_persona_style(monkeypatch):
    """With directives queued, the compose prompt carries the peak reminder and
    the system prompt ends with the compliance check (PersonaAction's layers)."""
    ra = ReplyAction()
    _patch_agent(monkeypatch)
    model = MagicMock()
    model.generate = AsyncMock(return_value="Welcome aboard. Your order shipped.")

    async def _ma(self, required=False):
        return model

    monkeypatch.setattr(ReplyAction, "get_model_action", _ma)
    v = _visitor_with(directives=[{"content": "Introduce yourself by name."}])
    await ra.reply("Your order shipped.", v)
    kwargs = model.generate.call_args.kwargs
    # recency: compliance check is present in the system prompt
    assert "COMPLIANCE CHECK" in kwargs["system"]
    # peak: the reminder rides in the compose prompt (user-turn slot)
    assert "MANDATORY directive" in kwargs["prompt"]


async def test_respond_generates_in_identity_and_publishes(monkeypatch):
    ra = ReplyAction()
    _patch_agent(monkeypatch, role="a guide")
    model = MagicMock()
    model.generate = AsyncMock(return_value="Voiced answer.")

    async def _ma(self, required=False):
        return model

    monkeypatch.setattr(ReplyAction, "get_model_action", _ma)

    v = _visitor_with()
    out = await ra.respond(v.interaction, visitor=v, text="raw answer")

    assert out == "Voiced answer."
    assert v.interaction.response == "Voiced answer."
    kwargs = model.generate.call_args.kwargs
    assert "You are Ada, a guide." in kwargs["system"]  # identity drives the voice
    # The message is RELAYED (framed as a "Tell the user: ..." directive) rather
    # than passed as the prompt — otherwise the model reacts to it ("That's
    # correct. ...") instead of delivering it.
    assert "Tell the user: raw answer" in kwargs["system"]
    assert "MANDATORY" in kwargs["system"]


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


# --- conversation history is applied when composing (directive-only finalize) ---


def _model(monkeypatch, ret="What is your email?"):
    model = MagicMock()
    model.generate = AsyncMock(return_value=ret)

    async def _ma(self, required=False):
        return model

    monkeypatch.setattr(ReplyAction, "get_model_action", _ma)
    return model


async def test_respond_sources_history_from_conversation(monkeypatch):
    """respond() with only a queued directive pulls conversation history from the
    visitor so the compose model has turn context (no blind clarifying reply)."""
    ra = ReplyAction()
    _patch_agent(monkeypatch)
    model = _model(monkeypatch)
    v = _visitor_with(directives=[{"content": "Ask: What is your email?"}])
    hist = [
        {"role": "user", "content": "Monday at 9am"},
        {"role": "assistant", "content": "What times are you available?"},
    ]
    v.conversation = SimpleNamespace(
        get_interaction_history=AsyncMock(return_value=hist)
    )
    await ra.respond(v.interaction, visitor=v)
    assert model.generate.call_args.kwargs["history"] == hist


async def test_respond_include_history_false_composes_blind(monkeypatch):
    ra = ReplyAction()
    ra.include_history = False
    _patch_agent(monkeypatch)
    model = _model(monkeypatch)
    v = _visitor_with(directives=[{"content": "Ask: What is your email?"}])
    v.conversation = SimpleNamespace(
        get_interaction_history=AsyncMock(
            return_value=[{"role": "user", "content": "x"}]
        )
    )
    await ra.respond(v.interaction, visitor=v)
    assert model.generate.call_args.kwargs["history"] == []


async def test_respond_respects_explicit_history(monkeypatch):
    ra = ReplyAction()
    _patch_agent(monkeypatch)
    model = _model(monkeypatch)
    v = _visitor_with(directives=[{"content": "Ask: What is your email?"}])
    # conversation would yield this, but an explicit arg must win:
    v.conversation = SimpleNamespace(
        get_interaction_history=AsyncMock(
            return_value=[{"role": "user", "content": "conv"}]
        )
    )
    explicit = [{"role": "user", "content": "explicit"}]
    await ra.respond(v.interaction, visitor=v, history=explicit)
    assert model.generate.call_args.kwargs["history"] == explicit


async def test_respond_is_a_conduit_never_answers_utterance(monkeypatch):
    """ReplyAction is a conduit: with no passed text and no queued
    directives/parameters it emits nothing — it never answers the user's
    utterance on its own, even when one is present the model could answer."""
    ra = ReplyAction()
    _patch_agent(monkeypatch)
    model = MagicMock()
    model.generate = AsyncMock(return_value="Paris.")

    async def _ma(self, required=False):
        return model

    monkeypatch.setattr(ReplyAction, "get_model_action", _ma)
    v = _visitor_with()  # no directives, no parameters
    v.interaction.utterance = "What is the capital of France?"
    out = await ra.respond(v.interaction, visitor=v)
    assert out == ""
    model.generate.assert_not_awaited()


async def test_respond_uses_byok_primary_model(monkeypatch):
    from jvagent.action.model.context import bind_model_override

    ra = ReplyAction()
    ra.model = "yaml-mini"
    _patch_agent(monkeypatch)
    model = MagicMock()
    captured = {}

    async def _gen(**kwargs):
        captured.update(kwargs)
        return "Hello."

    model.generate = _gen

    async def _ga(self, name):
        return model if name == "OpenAILanguageModelAction" else None

    monkeypatch.setattr(ReplyAction, "get_action", _ga)
    v = _visitor_with(directives=[{"content": "Be brief."}])
    with bind_model_override(
        {
            "slots": {
                "default": {
                    "provider": "openai",
                    "model": "byok-primary",
                    "api_key": "sk-test",
                },
                "light": {
                    "provider": "openai",
                    "model": "byok-secondary",
                    "api_key": "sk-test",
                },
            },
        }
    ):
        await ra.reply("answer", v)
    assert captured.get("model") == "byok-primary"
