"""Walk-path curation (ADR-0012): tool-exposed (routable) IAs are dropped from
the weight chain so they don't self-execute every turn; self + always_execute +
non-routable actions are kept."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

pytestmark = pytest.mark.asyncio


async def test_routable_ia_dropped_from_walk_path(
    make_skill_executive, make_visitor, flow_stub_cls
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
    ex = make_skill_executive(actions=[signup, intro, retrieval])

    v = make_visitor(utterance="hello")
    captured = {}
    v.curate_walk_path = AsyncMock(side_effect=lambda keep: captured.update(keep=keep))

    await ex._curate_walk_path(v)

    kept = captured["keep"]
    assert ex in kept  # the orchestrator itself
    assert intro in kept  # always_execute IA
    assert retrieval in kept  # non-routable IA stays in the chain
    assert signup not in kept  # routable/tool IA omitted from the walk path


async def test_curate_noops_without_curate_api(make_skill_executive, make_visitor):
    ex = make_skill_executive(actions=[])
    v = make_visitor(utterance="hi")
    v.curate_walk_path = None  # walker without a callable curate API
    await ex._curate_walk_path(v)  # must not raise
