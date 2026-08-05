"""Sibling-coverage advisory for ``channel_overrides`` (config lint).

``channel_overrides`` is resolved by the EXACT ``visitor.channel`` string, so a
block written for ``whatsapp`` does nothing on a ``whatsapp_call`` (voice) turn —
the action-level value applies instead, silently. Both keys are valid, so no
key-validity check can catch it; the only available signal is that one member of
a channel family is configured while its reachable sibling is not.

The lint is advisory: it is a heuristic about intent, and a deliberate
asymmetry is legitimate. It must never fail ``jvagent validate`` by default.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from jvagent.core.agent_yaml_validator import (
    AgentYamlWarning,
    _reset_warning_cache_for_tests,
    validate_agent_yaml,
)


def _agent(actions: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "agent": "test_agent",
        "version": "0.0.1",
        "author": "test",
        "jvagent": "0.1.0",
        "actions": actions,
    }


def _orchestrator(overrides: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "action": "jvagent/orchestrator",
        "context": {"channel_overrides": overrides},
    }


def _advisories(data: Dict[str, Any]) -> List[AgentYamlWarning]:
    _reset_warning_cache_for_tests()
    return [w for w in validate_agent_yaml(data) if w.severity == "advisory"]


def _warnings(data: Dict[str, Any]) -> List[AgentYamlWarning]:
    _reset_warning_cache_for_tests()
    return [w for w in validate_agent_yaml(data) if w.severity == "warning"]


def test_missing_sibling_override_is_advised():
    """The reported incident: skill_only_tools set for whatsapp only, with the
    voice action enabled, so voice turns silently use the action-level list."""
    data = _agent(
        [
            _orchestrator({"whatsapp": {"skill_only_tools": []}}),
            {"action": "jvagent/whatsapp_action"},
            {"action": "jvagent/whatsapp_voice_action"},
        ]
    )
    found = _advisories(data)
    assert len(found) == 1
    message = found[0].message
    assert "skill_only_tools" in message
    assert "'whatsapp'" in message and "'whatsapp_call'" in message
    # The consequence is the actionable part — the omission alone is often fine.
    assert "will use the action-level" in message
    assert "jvagent/whatsapp_voice_action" in message
    assert found[0].path.endswith("channel_overrides")


def test_advisory_does_not_fail_validation():
    """Advisories must not surface as warnings — they must not break CI."""
    data = _agent(
        [
            _orchestrator({"whatsapp": {"denied_tools": ["pay__*"]}}),
            {"action": "jvagent/whatsapp_voice_action"},
        ]
    )
    assert _advisories(data)
    assert _warnings(data) == []


def test_no_advisory_when_the_sibling_is_unreachable():
    """No voice action installed → voice turns cannot occur → say nothing."""
    data = _agent(
        [
            _orchestrator({"whatsapp": {"skill_only_tools": []}}),
            {"action": "jvagent/whatsapp_action"},
        ]
    )
    assert _advisories(data) == []


def test_no_advisory_when_both_siblings_set_the_key():
    data = _agent(
        [
            _orchestrator(
                {
                    "whatsapp": {"skill_only_tools": []},
                    "whatsapp_call": {"skill_only_tools": ["pay__*"]},
                }
            ),
            {"action": "jvagent/whatsapp_voice_action"},
        ]
    )
    assert _advisories(data) == []


def test_sibling_block_exists_but_omits_the_key():
    """A sibling block that sets other knobs still misses THIS one."""
    data = _agent(
        [
            _orchestrator(
                {
                    "whatsapp": {"skill_only_tools": []},
                    "whatsapp_call": {"history_limit": 4},
                }
            ),
            {"action": "jvagent/whatsapp_voice_action"},
        ]
    )
    found = _advisories(data)
    assert len(found) == 1
    assert "skill_only_tools" in found[0].message


def test_non_coverage_sensitive_keys_are_ignored():
    """Per-channel divergence in history_limit / acks / prompt extra is normal."""
    data = _agent(
        [
            _orchestrator(
                {
                    "whatsapp": {
                        "history_limit": 4,
                        "system_prompt_extra": "be brief",
                        "ack_statements": ["one moment"],
                    }
                }
            ),
            {"action": "jvagent/whatsapp_voice_action"},
        ]
    )
    assert _advisories(data) == []


def test_advisory_is_symmetric():
    """Gating voice but not chat is as likely a mistake as the reverse."""
    data = _agent(
        [
            _orchestrator({"whatsapp_call": {"pinned_tools": ["wa__send_flow"]}}),
            {"action": "jvagent/whatsapp_action"},
            {"action": "jvagent/whatsapp_voice_action"},
        ]
    )
    found = _advisories(data)
    assert len(found) == 1
    assert "'whatsapp_call'" in found[0].message
    assert "jvagent/whatsapp" in found[0].message


def test_one_advisory_per_missing_key():
    data = _agent(
        [
            _orchestrator(
                {"whatsapp": {"skill_only_tools": [], "denied_tools": ["x__*"]}}
            ),
            {"action": "jvagent/whatsapp_voice_action"},
        ]
    )
    found = _advisories(data)
    assert len(found) == 2
    flagged = {
        key
        for key in ("skill_only_tools", "denied_tools", "pinned_tools")
        if any(f"'{key}'" in w.message for w in found)
    }
    assert flagged == {"skill_only_tools", "denied_tools"}


def test_channel_with_no_family_is_ignored():
    """``email`` has no sibling — nothing to compare against."""
    data = _agent(
        [
            _orchestrator({"email": {"skill_only_tools": []}}),
            {"action": "jvagent/email_action"},
            {"action": "jvagent/whatsapp_voice_action"},
        ]
    )
    assert _advisories(data) == []


def test_no_channel_overrides_produces_no_advisories():
    data = _agent([{"action": "jvagent/orchestrator", "context": {}}])
    assert _advisories(data) == []


def test_malformed_override_block_is_ignored_not_crashed():
    """A non-mapping block is someone else's warning to raise, not a crash here."""
    data = _agent(
        [
            _orchestrator({"whatsapp": ["not", "a", "mapping"]}),
            {"action": "jvagent/whatsapp_voice_action"},
        ]
    )
    assert _advisories(data) == []


def test_channel_providers_match_real_action_package_names():
    """Every CHANNEL_PROVIDERS ref must be a real action's ``package.name``.

    This is the test that matters most here. The provider refs are the lint's
    reachability gate, so a wrong one makes the whole check silently never fire
    — and every behavioral test above still passes, because they use the same
    wrong constant as their fixture. The first draft of this map used the
    package DIRECTORY names (`jvagent/whatsapp_voice`) rather than the published
    package names (`jvagent/whatsapp_voice_action`), which differ. Only reading
    the real info.yaml catches that.
    """
    import yaml

    from jvagent.core.channel import CHANNEL_PROVIDERS

    root = Path(__file__).resolve().parents[2] / "jvagent" / "action"
    published = set()
    for info in root.rglob("info.yaml"):
        try:
            data = yaml.safe_load(info.read_text(encoding="utf-8")) or {}
        except Exception:  # pragma: no cover - unreadable package metadata
            continue
        name = (data.get("package") or {}).get("name")
        if isinstance(name, str):
            published.add(name)

    assert published, "no action packages discovered — test harness is broken"
    unknown = {
        channel: ref
        for channel, ref in CHANNEL_PROVIDERS.items()
        if ref not in published
    }
    assert not unknown, (
        f"CHANNEL_PROVIDERS references non-existent action packages: {unknown}. "
        "Use the 'package.name' from the action's info.yaml, not its directory."
    )


def test_existing_warnings_keep_default_severity():
    """The new field must not reclassify any pre-existing structural warning."""
    _reset_warning_cache_for_tests()
    found = validate_agent_yaml(_agent([{"action": "no_namespace"}]))
    assert found
    assert all(w.severity == "warning" for w in found)
