"""Tests for jvagent app/agent scaffolding."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from jvagent.scaffold.operations import (
    CreateAgentContext,
    CreateAppContext,
    create_agent_in_app,
    create_app,
)
from jvagent.scaffold.profile_resolve import (
    merge_action_lists,
    parse_agent_spec,
    resolve_profile_actions,
)


def test_merge_action_lists_last_wins() -> None:
    a = [{"action": "jvagent/a", "context": {"x": 1}}]
    b = [{"action": "jvagent/a", "context": {"x": 2}}]
    m = merge_action_lists(a, b)
    assert len(m) == 1
    assert m[0]["context"]["x"] == 2


def test_parse_agent_spec() -> None:
    assert parse_agent_spec("jvagent/bot") == ("jvagent/bot", None)
    assert parse_agent_spec("acme/bot@minimal") == ("acme/bot", "minimal")


def test_resolve_minimal_profile() -> None:
    actions = resolve_profile_actions(None, "minimal")
    ids = {x["action"] for x in actions}
    assert "jvagent/interact_router" in ids
    assert "jvagent/converse_interact_action" in ids


def test_resolve_conversational_extends(tmp_path: Path) -> None:
    actions = resolve_profile_actions(str(tmp_path), "conversational")
    ids = {x["action"] for x in actions}
    assert "jvagent/intro_interact_action" in ids


def test_resolve_bridge_profile_includes_bridge_and_helms() -> None:
    """The bridge profile produces a complete Reflex+Reasoning starter agent.

    Bridge ships the orchestrator plus the two default helms (ReflexHelm
    handles trivial turns, ReasoningHelm handles deliberate ones), plus
    persona for delivery polish and intro / handoff IAs that the
    reasoning router can route to. Persona stylisation happens directly
    inside Bridge on engine-final EMITs via ``deliver_via_persona``.
    """
    actions = resolve_profile_actions(None, "bridge")
    ids = {x["action"] for x in actions}
    assert "jvagent/bridge" in ids
    assert "jvagent/reflex_helm" in ids
    assert "jvagent/reasoning_helm" in ids
    assert "jvagent/persona" in ids
    assert "jvagent/openai_lm" in ids
    assert "jvagent/intro_interact_action" in ids
    assert "jvagent/handoff_interact_action" in ids


def test_create_app_default_profile_is_bridge(tmp_path: Path) -> None:
    """Calling create_app with no explicit default_profile picks bridge.

    Existing apps (those that already have agent.yaml files) are NOT
    affected — the default_profile only governs newly-scaffolded agents.
    """
    out = tmp_path / "bridge_app"
    create_app(
        CreateAppContext(
            output_dir=out,
            app_id="bg_app",
            title="Bg App",
            description="Desc",
            author="Tester",
            agent_specs=["jvagent/bot"],  # no @profile → default kicks in
            copy_builtin_profiles=False,
            init_git=False,
        )
    )
    agent_yaml_path = out / "agents" / "jvagent" / "bot" / "agent.yaml"
    assert agent_yaml_path.is_file()
    with open(agent_yaml_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    action_ids = {a.get("action") for a in (data.get("actions") or [])}
    assert "jvagent/bridge" in action_ids
    assert "jvagent/reasoning_helm" in action_ids
    assert "jvagent/persona" in action_ids


def test_create_app_minimal(tmp_path: Path) -> None:
    out = tmp_path / "app1"
    create_app(
        CreateAppContext(
            output_dir=out,
            app_id="test_app",
            title="Test App",
            description="Desc",
            author="Tester",
            agent_specs=["jvagent/bot@minimal"],
            default_profile="minimal",
            copy_builtin_profiles=False,
            init_git=False,
        )
    )
    assert (out / "app.yaml").is_file()
    assert (out / "agents" / "jvagent" / "bot" / "agent.yaml").is_file()
    with open(out / "app.yaml", "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    assert "jvagent/bot" in data["agents"]
    assert "database" not in data.get("config", {})
    assert "logging" not in data.get("config", {})
    assert "paths" not in data.get("config", {})
    assert "admin" not in data.get("config", {})
    assert "host" not in data.get("config", {}).get("server", {})
    assert "file_storage_provider" not in data.get("context", {})


def test_agent_create_adds_to_app(tmp_path: Path) -> None:
    create_app(
        CreateAppContext(
            output_dir=tmp_path,
            app_id="x",
            title="X",
            description="D",
            author="A",
            agent_specs=["jvagent/first@minimal"],
            copy_builtin_profiles=False,
            init_git=False,
        )
    )
    create_agent_in_app(
        CreateAgentContext(
            app_root=tmp_path,
            agent_spec="acme/second@minimal",
        )
    )
    with open(tmp_path / "app.yaml", "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    assert "jvagent/first" in data["agents"]
    assert "acme/second" in data["agents"]
    assert (tmp_path / "agents" / "acme" / "second" / "agent.yaml").is_file()


def test_duplicate_agent_in_yaml_raises(tmp_path: Path) -> None:
    create_app(
        CreateAppContext(
            output_dir=tmp_path,
            app_id="x",
            title="X",
            description="D",
            author="A",
            agent_specs=["jvagent/first@minimal"],
            copy_builtin_profiles=False,
            init_git=False,
        )
    )
    with pytest.raises(ValueError, match="already listed in app.yaml"):
        create_agent_in_app(
            CreateAgentContext(
                app_root=tmp_path,
                agent_spec="jvagent/first@minimal",
            )
        )


def test_create_app_email_updates_env_example(tmp_path: Path) -> None:
    out = tmp_path / "app_email"
    create_app(
        CreateAppContext(
            output_dir=out,
            app_id="email_app",
            title="Email App",
            description="Desc",
            author="Tester",
            agent_specs=["jvagent/bot@minimal"],
            default_profile="minimal",
            copy_builtin_profiles=False,
            init_git=False,
            admin_email="ops@example.com",
        )
    )
    env_example = (out / ".env.example").read_text(encoding="utf-8")
    assert "JVAGENT_ADMIN_EMAIL=ops@example.com" in env_example
