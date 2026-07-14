"""Phase 3 — end-to-end Orchestrator loop + continuation smoke (ADR-0012).

Drives the real ``execute`` with continuation, tool assembly, and the loop live;
only the model decision (``_run_model``) and the leaf publishes are mocked.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

from jvagent.memory.task_store import TaskStore


def _contents(log):
    return [e["content"] for e in log]


async def test_trivial_turn_uses_reply_tool(
    make_orchestrator, make_visitor, publish_log, monkeypatch
):
    """A greeting → the model calls the persona ``reply`` tool → one reply."""
    from jvagent.action.reply.reply_action import ReplyAction

    reply = ReplyAction()

    async def _pipe(self, text, interaction, visitor, streaming=False, transient=False):
        visitor.interaction.response = (visitor.interaction.response or "") + text

    monkeypatch.setattr(ReplyAction, "_pipe_response", _pipe)

    ex = make_orchestrator(
        actions=[reply],
        decisions=[
            {"action": "tool", "tool": "reply", "args": {"text": "Hey there!"}},
            {"action": "final", "answer": ""},
        ],
    )
    v = make_visitor(utterance="hi")
    await ex.execute(v)
    assert v.interaction.response == "Hey there!"


async def test_reply_with_answer_key_is_salvaged(
    make_orchestrator, make_visitor, publish_log, monkeypatch
):
    """Regression: model emits {"action":"reply","answer":"..."} (text in
    'answer', not 'args.text'). The normalizer must salvage it, voice once, and
    end the turn — not loop until the budget exhausts (live-smoke 2026-05-30)."""
    from jvagent.action.reply.reply_action import ReplyAction

    reply = ReplyAction()

    async def _pipe(self, text, interaction, visitor, streaming=False, transient=False):
        visitor.interaction.response = (visitor.interaction.response or "") + text

    monkeypatch.setattr(ReplyAction, "_pipe_response", _pipe)

    # If the loop didn't end after the reply, this same decision would repeat.
    ex = make_orchestrator(
        actions=[reply],
        decisions=[{"action": "reply", "answer": "Hello! How can I help?"}] * 20,
    )
    v = make_visitor(utterance="Hello there")
    await ex.execute(v)
    assert v.interaction.response == "Hello! How can I help?"  # emitted once
    assert _contents(publish_log) == []  # no clarify fallback


async def test_no_emission_falls_back_to_clarify(
    make_orchestrator, make_visitor, publish_log
):
    ex = make_orchestrator(actions=[], decisions=[{"action": "final", "answer": ""}])
    v = make_visitor(utterance="???")
    await ex.execute(v)
    assert _contents(publish_log) == [ex.clarify_text]


async def test_first_entry_into_ia_flow_via_tool(
    make_orchestrator, make_visitor, publish_log, flow_stub_cls
):
    """The model routes a signup utterance to the IA tool, which starts the flow."""

    class SignupIA(flow_stub_cls):
        anchors = ["sign up for training", "register for training"]
        description = "Signup interview."
        ran = 0

        async def execute(self, visitor):
            self.ran += 1
            visitor.interaction.response = "What's your full name?"

    ia = SignupIA()
    ex = make_orchestrator(
        actions=[ia],
        decisions=[
            {"action": "tool", "tool": "SignupIA", "args": {}},
            {"action": "final", "answer": ""},
        ],
    )
    v = make_visitor(utterance="I'd like to sign up for training")
    await ex.execute(v)
    assert ia.ran == 1
    assert v.interaction.response == "What's your full name?"


async def test_active_flow_continued_when_model_selects_its_tool(
    make_orchestrator, make_visitor, publish_log, flow_stub_cls
):
    """An active flow is continued by the model selecting its tool; the terminal
    IA-tool ends the turn (model-mediated continuation, no force-resume)."""

    class SignupIA(flow_stub_cls):
        anchors = ["sign up for training"]
        description = "Signup interview."
        ran = 0

        async def execute(self, visitor):
            self.ran += 1
            visitor.interaction.response = "Next question?"

    ia = SignupIA()
    ex = make_orchestrator(
        actions=[ia],
        action_registry={"SignupIA": ia},
        decisions=[{"action": "tool", "tool": "SignupIA", "args": {}}],
    )
    v = make_visitor(utterance="Jane Doe")
    h = await TaskStore(v.conversation).create(
        title="signup",
        description="SignupIA",
        task_type="SKILL",
        owner_action="SignupIA",
    )
    await h.start()

    await ex.execute(v)
    assert ia.ran == 1
    assert v.interaction.response == "Next question?"  # terminal IA tool ended turn


async def test_active_flow_offtopic_routed_elsewhere_not_into_flow(
    make_orchestrator, make_visitor, publish_log, monkeypatch, flow_stub_cls
):
    """Off-topic during an active flow, model-mediated mode (lock_active_flow=
    False): the model routes elsewhere (reply/search) and the interview is NOT
    run — the 'Who is Eldon Marks' escape. (With the default hard lock the turn
    routes into the IA; that path is covered by test_flow_lock.py.)"""
    from jvagent.action.reply.reply_action import ReplyAction

    reply = ReplyAction()

    async def _pipe(self, text, interaction, visitor, streaming=False, transient=False):
        visitor.interaction.response = (visitor.interaction.response or "") + text

    monkeypatch.setattr(ReplyAction, "_pipe_response", _pipe)

    class SignupIA(flow_stub_cls):
        anchors = ["sign up for training"]
        description = "Signup interview."
        ran = 0

        async def execute(self, visitor):
            self.ran += 1

    ia = SignupIA()
    ex = make_orchestrator(
        actions=[reply, ia],
        action_registry={"SignupIA": ia, "ReplyAction": reply},
        decisions=[
            {"action": "tool", "tool": "reply", "args": {"text": "Eldon Marks is ..."}},
        ],
    )
    ex.lock_active_flow = False  # model-mediated continuation mode
    v = make_visitor(utterance="Who is Eldon Marks")
    h = await TaskStore(v.conversation).create(
        title="signup",
        description="SignupIA",
        task_type="SKILL",
        owner_action="SignupIA",
    )
    await h.start()

    await ex.execute(v)
    assert ia.ran == 0  # interview NOT engaged on off-topic
    assert v.interaction.response == "Eldon Marks is ..."


async def test_terminal_ia_tool_directives_rendered(
    make_orchestrator, make_visitor, publish_log, monkeypatch, flow_stub_cls
):
    """A terminal IA-tool that emits via directives (not response) has them
    rendered through the persona after the turn ends."""
    from jvagent.action.reply.reply_action import ReplyAction

    reply = ReplyAction()

    async def _respond(self, interaction, visitor=None, **kw):
        visitor.interaction.response = "What's your full name?"
        return visitor.interaction.response

    monkeypatch.setattr(ReplyAction, "respond", _respond)

    class SignupIA(flow_stub_cls):
        anchors = ["sign up for training"]
        description = "Signup interview."

        async def execute(self, visitor):
            await visitor.add_directive("Ask for the user's name.")

    ia = SignupIA()
    ex = make_orchestrator(
        actions=[reply, ia],
        action_registry={"SignupIA": ia, "ReplyAction": reply},
        decisions=[{"action": "tool", "tool": "SignupIA", "args": {}}],
    )
    v = make_visitor(utterance="I'd like to sign up for training")
    v.add_directive = AsyncMock()
    v.interaction.get_unexecuted_directives = lambda: [{"content": "Ask for name."}]

    await ex.execute(v)
    assert v.interaction.response == "What's your full name?"
    assert _contents(publish_log) == []  # via persona, not clarify fallback
