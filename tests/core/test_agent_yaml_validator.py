"""Structural validation coverage for agent.yaml descriptors."""


def test_warn_agent_yaml_deduplicates(caplog):
    from jvagent.core.agent_yaml_validator import (
        _reset_warning_cache_for_tests,
        warn_agent_yaml,
    )

    _reset_warning_cache_for_tests()
    caplog.set_level("WARNING")
    payload = {
        "agent": "jvagent/example_agent",
        "unknown_top": "x",
        "actions": [{"action": "badformat"}],
    }

    warn_agent_yaml(payload, source="test-agent.yaml")
    warn_agent_yaml(payload, source="test-agent.yaml")

    assert caplog.text.count("unknown_top") == 1
    assert caplog.text.count("actions[0].action") == 1


def test_validate_agent_yaml_keeps_custom_action_payload_flexible():
    from jvagent.core.agent_yaml_validator import validate_agent_yaml

    payload = {
        "agent": "jvagent/example_agent",
        "context": {"alias": "Example", "custom_agent_property": "ok"},
        "actions": [
            {
                "action": "jvagent/interact_router",
                "context": {"custom_context_key": "value", "enabled": True},
                "config": {"custom_config_key": {"nested": True}},
            }
        ],
    }

    warnings = validate_agent_yaml(payload)
    assert not any("actions[0].context.custom_context_key" in w.path for w in warnings)
    assert not any("actions[0].config.custom_config_key" in w.path for w in warnings)


def test_validate_agent_yaml_warns_unexpected_action_entry_key():
    from jvagent.core.agent_yaml_validator import validate_agent_yaml

    payload = {
        "agent": "jvagent/example_agent",
        "actions": [
            {
                "action": "jvagent/interact_router",
                "unexpected": "x",
            }
        ],
    }
    warnings = validate_agent_yaml(payload)
    paths = {w.path for w in warnings}
    assert "actions[0].unexpected" in paths


def test_agent_loader_emits_validation_warning(tmp_path, caplog):
    from jvagent.core.agent_loader import AgentLoader
    from jvagent.core.agent_yaml_validator import _reset_warning_cache_for_tests

    _reset_warning_cache_for_tests()
    caplog.set_level("WARNING")

    agent_dir = tmp_path / "agents" / "jvagent" / "example_agent"
    agent_dir.mkdir(parents=True)
    (agent_dir / "agent.yaml").write_text(
        "\n".join(
            [
                "agent: jvagent/example_agent",
                "actions:",
                "  - action: jvagent/interact_router",
                "    unexpected: true",
            ]
        ),
        encoding="utf-8",
    )

    loader = AgentLoader(str(tmp_path))
    descriptor = loader.load_agent_descriptor("jvagent", "example_agent")
    assert descriptor is not None
    assert "actions[0].unexpected" in caplog.text


def test_action_loader_scan_emits_validation_warning(tmp_path, caplog):
    from jvagent.action.loader import ActionLoader
    from jvagent.core.agent_yaml_validator import _reset_warning_cache_for_tests

    _reset_warning_cache_for_tests()
    caplog.set_level("WARNING")

    agent_dir = tmp_path / "agents" / "jvagent" / "example_agent"
    agent_dir.mkdir(parents=True)
    (agent_dir / "agent.yaml").write_text(
        "\n".join(
            [
                "agent: jvagent/example_agent",
                "actions:",
                "  - action: jvagent/interact_router",
                "    extra_field: value",
            ]
        ),
        encoding="utf-8",
    )

    loader = ActionLoader(str(tmp_path))
    required = loader._scan_required_actions([("jvagent", "example_agent")])
    assert "jvagent/interact_router" in required
    assert "actions[0].extra_field" in caplog.text
