"""M2 — CapabilityRegistry unit tests (ADR-0010 §2.1). Synchronous."""

from __future__ import annotations

from jvagent.action.executive.registry import (
    Capability,
    CapabilityRegistry,
    build_registry_from_agent,
)
from jvagent.action.manifest import Manifest


def test_routing_view_tier_filtering():
    reg = CapabilityRegistry(
        [
            Capability(id="A", kind="ia", center="IACenter", tier=0),
            Capability(id="B", kind="skill", center="SkillsCenter", tier=2),
        ]
    )
    ids_all = [c["id"] for c in reg.routing_view()]
    assert ids_all == ["A", "B"]
    ids_t0 = [c["id"] for c in reg.routing_view(max_tier=0)]
    assert ids_t0 == ["A"]
    # routing lines carry no execution handle
    assert "handle" not in reg.routing_view()[0]


def test_match_anchor_substring_and_lowest_tier_wins():
    reg = CapabilityRegistry(
        [
            Capability(
                id="Hi", kind="ia", center="IACenter", anchors=("weather",), tier=5
            ),
            Capability(
                id="Lo", kind="ia", center="IACenter", anchors=("weather",), tier=1
            ),
        ]
    )
    assert reg.match_anchor("what is the WEATHER like").id == "Lo"
    assert reg.match_anchor("totally unrelated") is None


def test_match_anchor_regex():
    reg = CapabilityRegistry(
        [
            Capability(
                id="Book", kind="ia", center="IACenter", anchor_patterns=(r"\bbook\b",)
            )
        ]
    )
    assert reg.match_anchor("please book it").id == "Book"
    assert reg.match_anchor("notebook") is None  # word boundary


def test_by_kind_and_execution_view():
    reg = CapabilityRegistry(
        [
            Capability(id="A", kind="ia", center="IACenter"),
            Capability(id="B", kind="skill", center="SkillsCenter"),
        ]
    )
    assert [c.id for c in reg.by_kind("skill")] == ["B"]
    assert [c.id for c in reg.execution_view("ia")] == ["A"]


class _FakeIA:
    def __init__(self, anchors, manifest):
        self._anchors = anchors
        self._manifest = manifest
        self.description = "fake"

    @property
    def anchors(self):
        return self._anchors

    def get_manifest(self):
        return self._manifest


class WeatherIA(_FakeIA):
    pass


class OrchestratorIA(_FakeIA):
    pass


class AnchorlessIA(_FakeIA):
    pass


def test_build_registry_from_agent_filters_orchestrators_and_anchorless():
    actions = [
        WeatherIA(["weather"], Manifest(purpose="weather", latency_class="quick")),
        OrchestratorIA(["orchestrate"], Manifest(pattern_orchestrator=True)),
        AnchorlessIA([], Manifest(purpose="no anchors")),
    ]
    reg = build_registry_from_agent(
        None or object(), ia_center="IACenter", enabled_actions=actions
    )
    ids = [c.id for c in reg.all()]
    assert ids == ["WeatherIA"]  # orchestrator + anchorless excluded
    cap = reg.by_id("WeatherIA")
    assert cap.center == "IACenter" and cap.kind == "ia"
    assert cap.anchors == ("weather",)
