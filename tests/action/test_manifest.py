"""Tests for ``jvagent.action.manifest``.

Covers:
- Default values when payload is None or empty
- Validation of each field (string, list-of-string, bool, latency_class enum, optional float)
- Strict-mode raise vs lenient-mode log+default
- ``merged_with`` shallow merge semantics for agent.yaml overrides
- Round-trip serialisation via ``to_dict`` / ``from_payload``
- ``Action.get_manifest()`` accessor reads from ``self.metadata['manifest']``
- Loader ``ActionMetadata`` extracts the manifest block from info.yaml
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from jvagent.action.loader.metadata import ActionMetadata
from jvagent.action.manifest import (
    ACK_ELIGIBLE_LATENCY_CLASSES,
    DEFAULT_LATENCY_CLASS,
    DEFAULT_PATTERN_ORCHESTRATOR,
    DEFAULT_ROUTABLE_BY_ANCHOR,
    VALID_LATENCY_CLASSES,
    Manifest,
    ManifestValidationError,
)

# ---------------------------------------------------------------------------
# Defaults + happy path
# ---------------------------------------------------------------------------


def test_defaults_when_payload_is_none():
    m = Manifest.from_payload(None)
    assert m.purpose == ""
    assert m.activates_on == []
    assert m.terminates_when == []
    assert m.latency_class == DEFAULT_LATENCY_CLASS
    assert m.expected_duration_seconds is None
    assert m.routable_by_anchor is DEFAULT_ROUTABLE_BY_ANCHOR
    assert m.pattern_orchestrator is DEFAULT_PATTERN_ORCHESTRATOR


def test_pattern_orchestrator_default_false():
    m = Manifest.from_payload({})
    assert m.pattern_orchestrator is False


def test_pattern_orchestrator_true_when_set():
    m = Manifest.from_payload({"pattern_orchestrator": True})
    assert m.pattern_orchestrator is True


def test_pattern_orchestrator_must_be_bool_lenient():
    m = Manifest.from_payload({"pattern_orchestrator": "true"})
    assert m.pattern_orchestrator is DEFAULT_PATTERN_ORCHESTRATOR


def test_pattern_orchestrator_roundtrips():
    m = Manifest.from_payload(
        {"pattern_orchestrator": True, "routable_by_anchor": False}
    )
    out = m.to_dict()
    assert out["pattern_orchestrator"] is True
    assert out["routable_by_anchor"] is False
    assert Manifest.from_payload(out) == m


def test_executive_info_yaml_marks_pattern_orchestrator():
    """The Orchestrator's info.yaml must mark itself as the pattern orchestrator."""
    import yaml

    info_path = (
        Path(__file__).resolve().parents[2] / "jvagent/action/orchestrator/info.yaml"
    )
    with info_path.open() as fh:
        data = yaml.safe_load(fh)
    manifest_payload = data["package"]["manifest"]
    m = Manifest.from_payload(manifest_payload)
    assert m.pattern_orchestrator is True
    assert m.routable_by_anchor is False


def test_defaults_when_payload_is_empty_dict():
    m = Manifest.from_payload({})
    assert m == Manifest.from_payload(None)


def test_full_payload_parses_cleanly():
    payload = {
        "purpose": "Conduct a feedback interview.",
        "activates_on": ["user agrees", "scheduled by op"],
        "terminates_when": ["all questions answered", "user says STOP"],
        "latency_class": "deliberate",
        "expected_duration_seconds": 180.0,
    }
    m = Manifest.from_payload(payload)
    assert m.purpose == "Conduct a feedback interview."
    assert m.activates_on == ["user agrees", "scheduled by op"]
    assert m.latency_class == "deliberate"
    assert m.expected_duration_seconds == 180.0


def test_unknown_manifest_fields_ignored():
    """Legacy keys (e.g. removed turn_lock) are dropped silently."""
    m = Manifest.from_payload(
        {"purpose": "x", "turn_lock": True, "can_interrupt": False}
    )
    assert m.purpose == "x"
    assert not hasattr(m, "turn_lock")


# ---------------------------------------------------------------------------
# Latency class enum
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("cls", VALID_LATENCY_CLASSES)
def test_each_valid_latency_class_accepted(cls):
    m = Manifest.from_payload({"latency_class": cls})
    assert m.latency_class == cls


def test_latency_class_normalized_lowercase():
    m = Manifest.from_payload({"latency_class": "Deliberate"})
    assert m.latency_class == "deliberate"


def test_invalid_latency_class_falls_back_lenient():
    m = Manifest.from_payload({"latency_class": "blazing"})
    assert m.latency_class == DEFAULT_LATENCY_CLASS


def test_invalid_latency_class_raises_strict():
    with pytest.raises(ManifestValidationError) as exc_info:
        Manifest.from_payload({"latency_class": "blazing"}, strict=True)
    assert exc_info.value.field_name == "latency_class"


def test_ack_eligible_for_deliberate_and_long():
    for cls in ACK_ELIGIBLE_LATENCY_CLASSES:
        m = Manifest.from_payload({"latency_class": cls})
        assert m.is_ack_eligible()


def test_ack_not_eligible_for_instant_and_quick():
    for cls in ("instant", "quick"):
        m = Manifest.from_payload({"latency_class": cls})
        assert not m.is_ack_eligible()


# ---------------------------------------------------------------------------
# Field-level validation (lenient)
# ---------------------------------------------------------------------------


def test_purpose_must_be_string_lenient():
    m = Manifest.from_payload({"purpose": 42})
    assert m.purpose == ""  # default


def test_purpose_must_be_string_strict():
    with pytest.raises(ManifestValidationError):
        Manifest.from_payload({"purpose": 42}, strict=True)


def test_string_list_with_non_string_entry_drops_entry_lenient():
    m = Manifest.from_payload({"activates_on": ["valid", 123, "also valid"]})
    assert m.activates_on == ["valid", "also valid"]


def test_string_list_with_non_string_entry_raises_strict():
    with pytest.raises(ManifestValidationError):
        Manifest.from_payload({"activates_on": ["valid", 123]}, strict=True)


def test_string_list_must_be_list_lenient():
    m = Manifest.from_payload({"activates_on": "not a list"})
    assert m.activates_on == []


def test_expected_duration_accepts_int_and_float():
    assert (
        Manifest.from_payload(
            {"expected_duration_seconds": 30}
        ).expected_duration_seconds
        == 30.0
    )
    assert (
        Manifest.from_payload(
            {"expected_duration_seconds": 1.5}
        ).expected_duration_seconds
        == 1.5
    )


def test_expected_duration_rejects_negative():
    m = Manifest.from_payload({"expected_duration_seconds": -1})
    assert m.expected_duration_seconds is None


def test_expected_duration_rejects_string_lenient():
    m = Manifest.from_payload({"expected_duration_seconds": "30s"})
    assert m.expected_duration_seconds is None


# ---------------------------------------------------------------------------
# Non-dict payload
# ---------------------------------------------------------------------------


def test_non_dict_payload_returns_defaults_lenient():
    m = Manifest.from_payload("not a dict")
    assert m == Manifest.from_payload(None)


def test_non_dict_payload_raises_strict():
    with pytest.raises(ManifestValidationError):
        Manifest.from_payload(["not", "a", "dict"], strict=True)


# ---------------------------------------------------------------------------
# Merge semantics
# ---------------------------------------------------------------------------


def test_merged_with_none_returns_self():
    m = Manifest.from_payload({"latency_class": "deliberate"})
    assert m.merged_with(None) is m
    assert m.merged_with({}) is m


def test_merged_with_overrides_only_present_fields():
    base = Manifest.from_payload(
        {
            "purpose": "base",
            "latency_class": "deliberate",
        }
    )
    merged = base.merged_with({"latency_class": "quick"})
    assert merged.purpose == "base"  # preserved
    assert merged.latency_class == "quick"  # overridden


def test_merged_with_invalid_override_fails_consistently_with_load():
    base = Manifest.from_payload({"latency_class": "quick"})
    merged = base.merged_with({"latency_class": "bogus"})  # lenient → default
    assert merged.latency_class == DEFAULT_LATENCY_CLASS


# ---------------------------------------------------------------------------
# Serialisation round-trip
# ---------------------------------------------------------------------------


def test_to_dict_round_trip():
    payload = {
        "purpose": "X",
        "activates_on": ["a", "b"],
        "terminates_when": ["c"],
        "latency_class": "long",
        "expected_duration_seconds": 60.0,
    }
    m = Manifest.from_payload(payload)
    assert Manifest.from_payload(m.to_dict()) == m


# ---------------------------------------------------------------------------
# Loader integration
# ---------------------------------------------------------------------------


def test_action_metadata_extracts_manifest_block():
    info_data = {
        "package": {
            "name": "jvagent/test_action",
            "archetype": "TestAction",
            "manifest": {
                "purpose": "loader test",
                "latency_class": "instant",
            },
        }
    }
    md = ActionMetadata(info_data, Path("/tmp/test"), namespace="jvagent")
    assert md.manifest == {"purpose": "loader test", "latency_class": "instant"}


def test_action_metadata_missing_manifest_resolves_to_none():
    info_data = {
        "package": {
            "name": "jvagent/test_action",
            "archetype": "TestAction",
        }
    }
    md = ActionMetadata(info_data, Path("/tmp/test"), namespace="jvagent")
    assert md.manifest is None


def test_action_metadata_non_dict_manifest_yields_none():
    info_data = {
        "package": {
            "name": "jvagent/test_action",
            "archetype": "TestAction",
            "manifest": "not a dict",
        }
    }
    md = ActionMetadata(info_data, Path("/tmp/test"), namespace="jvagent")
    assert md.manifest is None


# ---------------------------------------------------------------------------
# Action.get_manifest()
# ---------------------------------------------------------------------------


def test_action_get_manifest_with_payload():
    from jvagent.action.base import Action

    action = MagicMock(spec=Action)
    action.metadata = {"manifest": {"latency_class": "deliberate"}}
    result = Action.get_manifest(action)
    assert isinstance(result, Manifest)
    assert result.latency_class == "deliberate"


def test_action_get_manifest_with_no_metadata():
    from jvagent.action.base import Action

    action = MagicMock(spec=Action)
    action.metadata = {}
    result = Action.get_manifest(action)
    assert isinstance(result, Manifest)
    assert result.latency_class == DEFAULT_LATENCY_CLASS


def test_action_get_manifest_with_none_metadata():
    from jvagent.action.base import Action

    action = MagicMock(spec=Action)
    action.metadata = None
    result = Action.get_manifest(action)
    assert isinstance(result, Manifest)
    assert result == Manifest.from_payload(None)
