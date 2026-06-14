"""Validation and coercion coverage for app.yaml processing."""


def test_warn_app_yaml_descriptor_unexpected_keys_are_deduplicated(caplog):
    from jvagent.core.app_yaml_validator import (
        _reset_warning_cache_for_tests,
        warn_app_yaml_descriptor,
    )

    _reset_warning_cache_for_tests()
    caplog.set_level("WARNING")
    payload = {
        "app": "x",
        "context": {"name": "n", "unexpected_context_key": "x"},
        "config": {"unexpected_section": {"k": "v"}},
        "agents": [],
    }

    warn_app_yaml_descriptor(payload, source="test-app.yaml")
    warn_app_yaml_descriptor(payload, source="test-app.yaml")

    text = caplog.text
    assert text.count("context.unexpected_context_key") == 1
    assert text.count("config.unexpected_section") == 1


def test_load_app_config_warns_unexpected_key(tmp_path, caplog):
    from jvagent.core.app_yaml_validator import _reset_warning_cache_for_tests
    from jvagent.core.config import load_app_config

    _reset_warning_cache_for_tests()
    caplog.set_level("WARNING")
    app_yaml = tmp_path / "app.yaml"
    app_yaml.write_text(
        "\n".join(
            [
                "app: sample",
                "config:",
                "  unknown_section:",
                "    username: admin",
            ]
        ),
        encoding="utf-8",
    )

    cfg = load_app_config(str(tmp_path))
    assert isinstance(cfg, dict)
    assert "config.unknown_section" in caplog.text


def test_load_app_yaml_app_id_resolves_placeholder(tmp_path, monkeypatch):
    from jvagent.core.config import load_app_yaml_app_id

    monkeypatch.setenv("APP_ID", "resolved_app")
    (tmp_path / "app.yaml").write_text("app: ${APP_ID}\n", encoding="utf-8")
    assert load_app_yaml_app_id(str(tmp_path)) == "resolved_app"


def test_get_performance_config_value_coerces_yaml_values():
    from jvagent.core.config import get_performance_config_value

    cfg = {
        "performance": {
            "enable_agent_cache": "false",
            "agent_cache_ttl": "45",
            "cache_cleanup_probability": "0.25",
        }
    }

    assert (
        get_performance_config_value(
            cfg,
            "enable_agent_cache",
            "JVAGENT_ENABLE_AGENT_CACHE",
            True,
            config_type=bool,
        )
        is False
    )
    assert (
        get_performance_config_value(
            cfg, "agent_cache_ttl", "JVAGENT_AGENT_CACHE_TTL", 300, config_type=int
        )
        == 45
    )
    assert (
        get_performance_config_value(
            cfg,
            "cache_cleanup_probability",
            "JVAGENT_CACHE_CLEANUP_PROBABILITY",
            0.1,
            config_type=float,
        )
        == 0.25
    )


def test_get_performance_config_value_env_precedence(monkeypatch):
    from jvagent.core.config import get_performance_config_value

    monkeypatch.setenv("JVAGENT_AGENT_CACHE_TTL", "90")
    cfg = {"performance": {"agent_cache_ttl": 45}}
    assert (
        get_performance_config_value(
            cfg, "agent_cache_ttl", "JVAGENT_AGENT_CACHE_TTL", 300, config_type=int
        )
        == 90
    )


def test_profiling_env_bool_tokens(monkeypatch):
    from jvagent.core.profiling import _get_profiling_config

    monkeypatch.setenv("JVAGENT_ENABLE_PROFILING", "on")
    assert _get_profiling_config() is True
    monkeypatch.setenv("JVAGENT_ENABLE_PROFILING", "off")
    assert _get_profiling_config() is False
