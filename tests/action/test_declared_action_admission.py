"""Declared Actions must not silently disappear from the admitted graph."""

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from jvagent.action.loader import ActionLoader
from jvagent.core.errors import ConfigError


@pytest.mark.parametrize(
    "declaration",
    [{}, {"action": 3}, {"action": "missing"}, {"action": "jvagent/not_installed"}],
)
def test_unresolved_declaration_fails_without_dumping_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    declaration: dict[str, Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    loader = ActionLoader(str(tmp_path))
    monkeypatch.setattr(loader, "discover_actions", lambda *_: [])
    monkeypatch.setattr(loader, "discover_core_action", lambda *_: None)
    declaration = {**declaration, "context": {"api_key": "private configuration value"}}
    with pytest.raises(ConfigError) as error:
        loader.load_actions_for_agent("test", "agent", "id", [declaration])
    assert "private configuration value" not in str(error.value)
    assert "private configuration value" not in caplog.text


def test_declared_constructor_failure_is_fatal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    loader = ActionLoader(str(tmp_path))
    metadata = SimpleNamespace(
        namespace="test", name="policy", enabled=True, config={}, archetype="Policy"
    )
    monkeypatch.setattr(loader, "discover_actions", lambda *_: [metadata])
    monkeypatch.setattr(loader, "create_action_instance", lambda *args, **kwargs: None)
    with pytest.raises(ConfigError):
        loader.load_actions_for_agent(
            "test", "agent", "id", [{"action": "test/policy"}]
        )


@pytest.mark.parametrize(
    "configs",
    [
        [{"action": "test/policy"}, {"action": "test/policy"}],
        [{"action": "test/policy"}, {"action": "test/other"}],
    ],
)
def test_ambiguous_security_declarations_are_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, configs: list[dict[str, str]]
) -> None:
    loader = ActionLoader(str(tmp_path))
    metadata = [
        SimpleNamespace(
            namespace="test", name=name, enabled=True, config={}, archetype="Policy"
        )
        for name in ["policy", "other"]
    ]
    monkeypatch.setattr(loader, "discover_actions", lambda *_: metadata)
    monkeypatch.setattr(
        loader, "create_action_instance", lambda *args, **kwargs: object()
    )
    with pytest.raises(ConfigError):
        loader.load_actions_for_agent("test", "agent", "id", configs)
