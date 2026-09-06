"""Walk-path curation (ADR-0012): tool-exposed (routable) IAs are dropped from
the weight chain so they don't self-execute every turn; self + always_execute +
non-routable actions are kept."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock


async def test_routable_ia_dropped_from_walk_path(
    make_orchestrator, make_visitor, flow_stub_cls
):
    class SignupIA(flow_stub_cls):
        anchors = ["sign up for training"]

        async def execute(self, visitor):
            pass

    class IntroIA(flow_stub_cls):
        always_execute = True
        anchors = []

        async def execute(self, visitor):
            pass

    class RetrievalIA(flow_stub_cls):
        anchors = []  # non-routable (no triggers) → stays in the chain

        async def execute(self, visitor):
            pass

    signup, intro, retrieval = SignupIA(), IntroIA(), RetrievalIA()
    ex = make_orchestrator(actions=[signup, intro, retrieval])

    v = make_visitor(utterance="hello")
    captured = {}
    v.curate_walk_path = AsyncMock(side_effect=lambda keep: captured.update(keep=keep))

    await ex._curate_walk_path(v)

    kept = captured["keep"]
    assert ex in kept  # the orchestrator itself
    assert intro in kept  # always_execute IA
    assert retrieval in kept  # non-routable IA stays in the chain
    assert signup not in kept  # routable/tool IA omitted from the walk path


async def test_plain_actions_excluded_from_walk_path_curation(
    make_orchestrator, make_visitor, flow_stub_cls
):
    """Non-InteractAction plugins (InterviewAction, LM, MCP, …) are not walk-path members."""

    class IntroIA(flow_stub_cls):
        always_execute = True
        anchors = []

        async def execute(self, visitor):
            pass

    intro = IntroIA()
    plain = MagicMock(name="InterviewAction")
    plain.get_tools = AsyncMock(return_value=[])
    ex = make_orchestrator(actions=[intro, plain])

    v = make_visitor(utterance="hello")
    captured = {}
    v.curate_walk_path = AsyncMock(side_effect=lambda keep: captured.update(keep=keep))

    await ex._curate_walk_path(v)

    kept = captured["keep"]
    assert ex in kept
    assert intro in kept
    assert plain not in kept


async def test_curate_noops_without_curate_api(make_orchestrator, make_visitor):
    ex = make_orchestrator(actions=[])
    v = make_visitor(utterance="hi")
    v.curate_walk_path = None  # walker without a callable curate API
    await ex._curate_walk_path(v)  # must not raise


async def test_only_still_queued_actions_are_handed_to_the_walker(
    make_orchestrator, make_visitor, flow_stub_cls
):
    """The orchestrator itself is executing (not queued) and a lower-weight
    always_execute IA has already run; handing them to ``curate_walk_path``
    made the walker log a "caller-supplied action(s) were not in the queue"
    line on every turn. Only what the walker still holds is passed."""

    class IntroIA(flow_stub_cls):
        always_execute = True
        anchors = []

        async def execute(self, visitor):
            pass

    class AuditIA(flow_stub_cls):
        always_execute = True
        anchors = []

        async def execute(self, visitor):
            pass

    intro, audit = IntroIA(), AuditIA()
    intro.id, audit.id = "n.IntroIA.1", "n.AuditIA.1"
    ex = make_orchestrator(actions=[intro, audit])
    v = make_visitor(utterance="hello")
    v.get_queue = AsyncMock(return_value=[audit])  # intro already ran; ex is running
    captured = {}
    v.curate_walk_path = AsyncMock(side_effect=lambda keep: captured.update(keep=keep))
    await ex._curate_walk_path(v)
    assert [a.id for a in captured["keep"]] == ["n.AuditIA.1"]
