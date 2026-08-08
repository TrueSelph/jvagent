"""``jvagent validate`` exit codes for advisory findings.

An advisory is a heuristic lint. It must be visible but must NOT fail an
existing pipeline — every current `AgentYamlWarning` is CI-fatal, so shipping a
heuristic at warning severity would break the build of any app whose config is
deliberately asymmetric. ``--strict`` is the opt-in for teams that want them
enforced.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from jvagent.cli.validate import run_validate

_APP_YAML = {
    "app": "probe_app",
    "version": "1.0.0",
    "author": "test",
    "context": {"name": "Probe App", "description": "advisory exit-code probe"},
    "agents": ["probe/bot"],
}


def _write_app(root: Path, overrides: dict) -> None:
    """An app whose only finding is the sibling-coverage advisory."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "app.yaml").write_text(yaml.safe_dump(_APP_YAML), encoding="utf-8")
    agent_dir = root / "agents" / "probe" / "bot"
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "agent.yaml").write_text(
        yaml.safe_dump(
            {
                "agent": "bot",
                "version": "0.0.1",
                "author": "test",
                "jvagent": "0.1.0",
                "actions": [
                    {
                        "action": "jvagent/orchestrator",
                        "context": {"channel_overrides": overrides},
                    },
                    {"action": "jvagent/whatsapp_voice_action"},
                ],
            }
        ),
        encoding="utf-8",
    )


def test_advisory_does_not_fail_validate(tmp_path):
    _write_app(tmp_path, {"whatsapp": {"skill_only_tools": []}})
    assert run_validate(str(tmp_path)) == 0


def test_strict_promotes_advisory_to_failure(tmp_path):
    _write_app(tmp_path, {"whatsapp": {"skill_only_tools": []}})
    assert run_validate(str(tmp_path), strict=True) == 1


def test_clean_config_passes_under_strict(tmp_path):
    """--strict must not invent findings — a covered config still passes."""
    _write_app(
        tmp_path,
        {
            "whatsapp": {"skill_only_tools": []},
            "whatsapp_call": {"skill_only_tools": []},
        },
    )
    assert run_validate(str(tmp_path), strict=True) == 0


def test_advisory_is_logged(tmp_path, caplog):
    _write_app(tmp_path, {"whatsapp": {"skill_only_tools": []}})
    with caplog.at_level("WARNING"):
        run_validate(str(tmp_path))
    assert "validate advisory" in caplog.text
    assert "whatsapp_call" in caplog.text
