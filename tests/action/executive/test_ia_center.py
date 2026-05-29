"""M6 — IA center (anchored rails) tests (ADR-0010 §2.1).

Fake rails IAs (no real LM). Exercises: run-from-brief, registry-anchor
fallback, not-found, AC denial, turn-lock → sustained activation, and the
directive→persona finalize path.
"""

from __future__ import annotations

import pytest

from jvagent.action.executive.centers.ia_center import IACenter
from jvagent.action.executive.contracts import ACTIVATE, Brief
from jvagent.action.executive.registry import Capability, CapabilityRegistry
from jvagent.action.manifest import Manifest

from .conftest import make_ac, make_agent_with_ac

pytestmark = pytest.mark.asyncio


def _contents(log):
    return [e["content"] for e in log]


class FakeIA:
    def __init__(
        self, *, publishes=None, directive=None, turn_lock=False, locking=True
    ):
        self.publishes = publishes
        self.directive = directive
        self.turn_lock = turn_lock
        self.locking = locking
        self.ran = False

    async def execute(self, visitor):
        self.ran = True
        if self.publishes is not None:
            visitor.interaction.response = self.publishes
        if self.directive is not None:
            visitor.interaction.directives.append(
                {"content": self.directive, "executed": False}
            )

    def get_manifest(self):
        return Manifest(turn_lock=self.turn_lock)

    async def is_actively_locking_turn(self, visitor):
        return self.locking


def _wire_ia_resolution(monkeypatch, registry_map):
    async def _get_action(self, name):
        return registry_map.get(name)

    monkeypatch.setattr(IACenter, "get_action", _get_action)


def _visitor_with_directives(make_visitor, utterance="hi"):
    v = make_visitor(utterance=utterance)
    v.interaction.directives = []
    v.interaction.response = ""
    return v


async def test_runs_ia_named_in_brief(
    make_executive, make_visitor, publish_log, monkeypatch
):
    fake = FakeIA(publishes="sunny and warm")
    _wire_ia_resolution(monkeypatch, {"WeatherIA": fake})
    ia_center = IACenter()
    ex = make_executive(
        centers={"IACenter": ia_center},
        executive_script=[
            ACTIVATE(
                "IACenter",
                brief=Brief(slots={"capability": "WeatherIA"}),
                on_done="voice",
            )
        ],
    )
    await ex.execute(_visitor_with_directives(make_visitor))
    assert fake.ran is True
    # IA owns its output (set response directly); executive does not re-publish.
    assert publish_log == []


async def test_registry_anchor_fallback(
    make_executive, make_visitor, publish_log, monkeypatch
):
    fake = FakeIA(publishes="booked")
    _wire_ia_resolution(monkeypatch, {"BookingIA": fake})
    registry = CapabilityRegistry(
        [
            Capability(
                id="BookingIA",
                kind="ia",
                center="IACenter",
                anchors=("book a table",),
                handle="BookingIA",
            )
        ]
    )
    ia_center = IACenter()
    ex = make_executive(
        centers={"IACenter": ia_center},
        executive_script=[ACTIVATE("IACenter", brief=Brief(), on_done="voice")],
        registry=registry,
    )
    await ex.execute(_visitor_with_directives(make_visitor, utterance="book a table"))
    assert fake.ran is True


async def test_not_found(make_executive, make_visitor, publish_log, monkeypatch):
    _wire_ia_resolution(monkeypatch, {})
    ia_center = IACenter()
    ia_center.not_found_text = "no such flow"
    ex = make_executive(
        centers={"IACenter": ia_center},
        executive_script=[
            ACTIVATE(
                "IACenter", brief=Brief(slots={"capability": "Ghost"}), on_done="voice"
            )
        ],
    )
    await ex.execute(_visitor_with_directives(make_visitor))
    assert _contents(publish_log) == ["no such flow"]


async def test_access_denied(make_executive, make_visitor, publish_log, monkeypatch):
    fake = FakeIA(publishes="secret")
    _wire_ia_resolution(monkeypatch, {"WeatherIA": fake})
    ac = make_ac(deny_labels={"tool:delegate:WeatherIA"})
    agent = make_agent_with_ac(ac)
    ia_center = IACenter()
    ia_center.access_denied_text = "denied"
    ex = make_executive(
        centers={"IACenter": ia_center},
        executive_script=[
            ACTIVATE(
                "IACenter",
                brief=Brief(slots={"capability": "WeatherIA"}),
                on_done="voice",
            )
        ],
        agent=agent,
    )
    await ex.execute(_visitor_with_directives(make_visitor))
    assert _contents(publish_log) == ["denied"]
    assert fake.ran is False


async def test_turn_lock_sustains(
    make_executive, make_visitor, publish_log, monkeypatch
):
    fake = FakeIA(publishes="Question 1?", turn_lock=True, locking=True)
    _wire_ia_resolution(monkeypatch, {"InterviewIA": fake})
    ia_center = IACenter()
    visitor = _visitor_with_directives(make_visitor)
    ex = make_executive(
        centers={"IACenter": ia_center},
        executive_script=[
            ACTIVATE(
                "IACenter",
                brief=Brief(slots={"capability": "InterviewIA"}),
                on_done="voice",
            )
        ],
    )
    await ex.execute(visitor)
    obs = visitor.interaction.parameters["executive_observability"]
    assert obs["suspended"] is not None
    assert obs["suspended"]["center"] == "IACenter"
    assert obs["suspended"]["brief"]["slots"]["capability"] == "InterviewIA"


async def test_turn_lock_released_does_not_sustain(
    make_executive, make_visitor, publish_log, monkeypatch
):
    fake = FakeIA(publishes="done", turn_lock=True, locking=False)
    _wire_ia_resolution(monkeypatch, {"InterviewIA": fake})
    ia_center = IACenter()
    visitor = _visitor_with_directives(make_visitor)
    ex = make_executive(
        centers={"IACenter": ia_center},
        executive_script=[
            ACTIVATE(
                "IACenter",
                brief=Brief(slots={"capability": "InterviewIA"}),
                on_done="voice",
            )
        ],
    )
    await ex.execute(visitor)
    obs = visitor.interaction.parameters["executive_observability"]
    assert obs["suspended"] is None


async def test_directive_finalize_calls_persona(
    make_executive, make_visitor, publish_log, monkeypatch
):
    fake = FakeIA(directive="ask their name")  # leaves a directive, no response
    _wire_ia_resolution(monkeypatch, {"SignupIA": fake})

    responded = {"called": False}

    async def _respond(self, visitor, directives=None, **kw):
        responded["called"] = True
        return "rendered"

    monkeypatch.setattr(IACenter, "respond", _respond)

    ia_center = IACenter()
    ex = make_executive(
        centers={"IACenter": ia_center},
        executive_script=[
            ACTIVATE(
                "IACenter",
                brief=Brief(slots={"capability": "SignupIA"}),
                on_done="voice",
            )
        ],
    )
    await ex.execute(_visitor_with_directives(make_visitor))
    assert responded["called"] is True
