"""Migration emits reviewable declarations without dropping policy or secrets."""

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

pytest.importorskip("pydantic_ai_harness")

from jvagent.cli.harness_migrate import handle_harness_migrate, plan_harness_migration
from jvagent.cli.main import DISPATCH, _first_app_root_path


@pytest.fixture
def app(tmp_path: Path) -> Path:
    directory = tmp_path / "agents" / "demo" / "bot"
    skill = directory / "skills" / "answer"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: answer\ndescription: Answer\n---\nAnswer concisely.\n"
    )
    data = {
        "agent": "demo/bot",
        "context": {
            "alias": "Helper",
            "role": "Answer questions",
            "interaction_limit": 30,
        },
        "actions": [
            {
                "action": "jvagent/orchestrator",
                "context": {
                    "enabled": True,
                    "model": "openai/gpt-4.1",
                    "model_action_type": "LiteLLMLanguageModelAction",
                    "model_max_tokens": 8192,
                    "activation_budget": 12,
                    "max_duration_seconds": 60,
                    "max_turn_cost_usd": 0.25,
                    "max_conversation_cost_usd": "1.50",
                    "system_prompt_extra": "Keep private: secret-example-value",
                },
            },
            {
                "action": "demo/modular_tool",
                "context": {
                    "api_key": "secret-example-value",
                    "nested": [1, {"setting": True}],
                },
            },
            {"action": "jvagent/reply", "context": {"enabled": True}},
        ],
    }
    (directory / "agent.yaml").write_text(yaml.safe_dump(data, sort_keys=False))
    return tmp_path


def source(app: Path) -> Path:
    return app / "agents/demo/bot/agent.yaml"


@pytest.mark.parametrize(
    "settings,blocked",
    [
        ({"enabled": False}, False),
        ({"enabled": True}, True),
        ({"enabled": True, "native_image": "python:latest"}, True),
        ({"enabled": True, "native_image": "python@sha256:" + "0" * 64}, False),
        (
            {
                "enabled": True,
                "native_image": "python@sha256:" + "0" * 64,
                "memory_mb": 0,
            },
            True,
        ),
    ],
)
def test_script_executor_migration_requires_explicit_native_policy(
    app: Path, settings: Any, blocked: bool
) -> None:
    document = yaml.safe_load(source(app).read_text())
    document["actions"].append(
        {"action": "jvagent/code_execution", "context": settings}
    )
    source(app).write_text(yaml.safe_dump(document))
    before = source(app).read_bytes()
    report, candidate = plan_harness_migration(app, "demo/bot", skill="answer")
    assert (candidate is None) == blocked
    assert bool(report["blockers"]) == blocked
    assert source(app).read_bytes() == before


def change(app: Path, **settings: Any) -> None:
    document = yaml.safe_load(source(app).read_text())
    document["actions"][0]["context"].update(settings)
    source(app).write_text(yaml.safe_dump(document))


def test_candidate_preserves_modules_identity_assets_and_limits(app: Path) -> None:
    before = source(app).read_bytes()
    original = yaml.safe_load(before)
    report, candidate = plan_harness_migration(app, "demo/bot", skill="answer")
    assert report["status"] == "candidate"
    assert report["blockers"] == []
    assert candidate is not None
    target = candidate["actions"][0]
    assert target["action"] == "jvagent/native_harness"
    assert candidate["actions"][1:] == original["actions"][1:]
    assert candidate["context"] == original["context"]
    context = target["context"]
    assert context["model"] == "openai:gpt-4.1"
    assert context["skill_directory"] == "agents/demo/bot/skills/answer"
    assert context["max_requests"] == 12
    assert context["timeout_seconds"] == 60
    assert context["max_run_cost_usd"] == "0.25"
    assert context["max_conversation_cost_usd"] == "1.50"
    assert "Helper" in context["instructions"]
    assert "secret-example-value" in context["instructions"]
    assert "secret-example-value" not in json.dumps(report)
    assert set(original["actions"][0]["context"]) <= set(report["mappings"])
    assert source(app).read_bytes() == before
    assert plan_harness_migration(app, "demo/bot", skill="answer")[0] == report
    candidate["actions"][1]["context"]["nested"].append("changed")
    assert source(app).read_bytes() == before


@pytest.mark.parametrize(
    "key",
    [
        "system_prompt",
        "tool_protocol",
        "unreviewed_light_model_policy",
        "model_fallbacks",
        "pilot_max_output_tokens",
        "misspelled_limit",
    ],
)
def test_unmapped_or_inactive_controls_block_output(app: Path, key: str) -> None:
    change(app, **{key: "secret-example-value"})
    report, candidate = plan_harness_migration(app, "demo/bot", skill="answer")
    assert report["status"] == "blocked" and candidate is None
    assert report["blockers"]
    assert "secret-example-value" not in json.dumps(report)


@pytest.mark.parametrize(
    "settings",
    [
        {"model_max_tokens": False},
        {"model_max_tokens": -1},
        {"activation_budget": True},
        {"activation_budget": 1.2},
        {"max_duration_seconds": float("inf")},
        {"max_duration_seconds": -1},
        {"max_turn_cost_usd": "NaN"},
        {"max_turn_cost_usd": -1},
        {"max_turn_cost_usd": True},
        {"model_temperature": False},
        {"model": "ollama/cloud"},
        {"model_action_type": []},
        {"skill_runtime": []},
    ],
)
def test_invalid_or_unqualified_values_fail_closed(
    app: Path, settings: dict[str, Any]
) -> None:
    change(app, **settings)
    report, candidate = plan_harness_migration(app, "demo/bot", skill="answer")
    assert report["status"] == "blocked" and candidate is None


def test_pilot_caps_are_preserved_in_native_library_limits(app: Path) -> None:
    document = yaml.safe_load(source(app).read_text())
    context = document["actions"][0]["context"]
    context.pop("activation_budget")
    context.pop("max_duration_seconds")
    context.update(
        skill_runtime="capability_pilot",
        pilot_skill="answer",
        pilot_max_model_requests=17,
        pilot_max_tool_calls=31,
        pilot_max_total_tokens=42000,
        pilot_max_output_tokens=7000,
        pilot_max_runtime_seconds=90,
    )
    source(app).write_text(yaml.safe_dump(document))
    report, candidate = plan_harness_migration(app, "demo/bot")
    assert report["status"] == "candidate"
    limits = candidate["actions"][0]["context"]
    assert {
        k: limits[k]
        for k in (
            "max_requests",
            "max_tool_calls",
            "max_tokens",
            "max_output_tokens",
            "timeout_seconds",
        )
    } == {
        "max_requests": 17,
        "max_tool_calls": 31,
        "max_tokens": 42000,
        "max_output_tokens": 7000,
        "timeout_seconds": 90,
    }


def test_disabled_old_caps_choose_reported_finite_defaults(app: Path) -> None:
    change(
        app,
        activation_budget=0,
        max_duration_seconds=0,
        max_turn_cost_usd="0.00",
        max_conversation_cost_usd=0,
        model_max_tokens=0,
    )
    report, candidate = plan_harness_migration(app, "demo/bot", skill="answer")
    assert report["status"] == "candidate"
    limits = candidate["actions"][0]["context"]
    assert limits["max_requests"] == 1
    assert limits["timeout_seconds"] == 180
    assert limits["max_run_cost_usd"] == "2.00"
    assert limits["model_settings"]["max_tokens"] == 8192
    assert {
        "timeout_seconds",
        "max_run_cost_usd",
        "max_conversation_cost_usd",
        "model_settings.max_tokens",
    } <= set(report["bounded_defaults"])
    assert "max_requests" not in report["bounded_defaults"]


def test_simple_model_service_is_retired_only_for_reviewed_callers(app: Path) -> None:
    data = yaml.safe_load(source(app).read_text())
    data["actions"] = [
        data["actions"][0],
        {"action": "jvagent/litellm_lm", "context": {"api_key": "${OPENAI_API_KEY}"}},
        data["actions"][2],
    ]
    source(app).write_text(yaml.safe_dump(data))
    report, candidate = plan_harness_migration(app, "demo/bot", skill="answer")
    assert report["status"] == "candidate"
    assert report["retired_actions"] == ["jvagent/litellm_lm"]
    assert [a["action"] for a in candidate["actions"]] == [
        "jvagent/native_harness",
        "jvagent/reply",
    ]
    data["actions"].append({"action": "demo/unknown_consumer"})
    source(app).write_text(yaml.safe_dump(data))
    report, candidate = plan_harness_migration(app, "demo/bot", skill="answer")
    assert report["status"] == "blocked" and candidate is None


@pytest.mark.parametrize(
    "settings",
    [
        {"api_key": "secret-example-value"},
        {"api_base": "https://other.invalid"},
        {"max_retries": 3},
        {"enabled": False},
    ],
)
def test_model_service_custom_policy_is_not_discarded(
    app: Path, settings: dict[str, Any]
) -> None:
    data = yaml.safe_load(source(app).read_text())
    data["actions"] = [
        data["actions"][0],
        {"action": "jvagent/litellm_lm", "context": settings},
        data["actions"][2],
    ]
    source(app).write_text(yaml.safe_dump(data))
    report, candidate = plan_harness_migration(app, "demo/bot", skill="answer")
    assert report["status"] == "blocked" and candidate is None
    assert "secret-example-value" not in json.dumps(report)


@pytest.mark.parametrize("skill", [None, "../answer", "missing"])
def test_missing_or_escaping_skill_blocks(app: Path, skill: str | None) -> None:
    report, candidate = plan_harness_migration(app, "demo/bot", skill=skill)
    assert report["status"] == "blocked" and candidate is None


def test_mixed_owners_block_and_native_rerun_is_noop(app: Path) -> None:
    document = yaml.safe_load(source(app).read_text())
    document["actions"].append({"action": "jvagent/native_harness"})
    source(app).write_text(yaml.safe_dump(document))
    report, candidate = plan_harness_migration(app, "demo/bot", skill="answer")
    assert report["status"] == "blocked" and candidate is None
    document["actions"].pop(0)
    source(app).write_text(yaml.safe_dump(document))
    report, candidate = plan_harness_migration(app, "demo/bot")
    assert report["status"] == "already_native" and candidate is None


def test_cli_dry_run_and_exclusive_private_candidate(
    app: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    before = source(app).read_bytes()
    args = ["--agent", "demo/bot", "--skill", "answer"]
    assert handle_harness_migrate(args, str(app)) == 0
    assert "secret-example-value" not in capsys.readouterr().out
    output = app / "candidate.yaml"
    assert not output.exists()
    assert handle_harness_migrate([*args, "--output", str(output)], str(app)) == 0
    assert output.stat().st_mode & 0o777 == 0o600
    generated = output.read_bytes()
    assert handle_harness_migrate([*args, "--output", str(output)], str(app)) == 2
    assert output.read_bytes() == generated and source(app).read_bytes() == before
    assert "secret-example-value" not in capsys.readouterr().out


def test_output_symlink_is_never_followed(app: Path) -> None:
    target = app / "untouched.yaml"
    output = app / "candidate.yaml"
    output.symlink_to(target)
    assert (
        handle_harness_migrate(
            ["--agent", "demo/bot", "--skill", "answer", "--output", str(output)],
            str(app),
        )
        == 2
    )
    assert not target.exists()


def test_malformed_yaml_never_echoes_values(
    app: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source(app).write_text("agent: [secret-example-value")
    assert (
        handle_harness_migrate(["--agent", "demo/bot", "--skill", "answer"], str(app))
        == 2
    )
    assert "secret-example-value" not in capsys.readouterr().out


def test_blocked_cli_does_not_create_candidate(app: Path) -> None:
    change(app, system_prompt="custom legacy grammar")
    output = app / "blocked.yaml"
    assert (
        handle_harness_migrate(
            ["--agent", "demo/bot", "--skill", "answer", "--output", str(output)],
            str(app),
        )
        == 2
    )
    assert not output.exists()


def test_source_symlink_cannot_escape_app(app: Path) -> None:
    outside = app.with_name(app.name + "-outside")
    outside.mkdir()
    foreign = outside / "agent.yaml"
    foreign.write_bytes(source(app).read_bytes())
    source(app).unlink()
    source(app).symlink_to(foreign)
    with pytest.raises(ValueError, match="escapes"):
        plan_harness_migration(app, "demo/bot", skill="answer")


def test_oversized_source_is_blocked_before_parsing(app: Path) -> None:
    source(app).write_bytes(b"x" * 1_000_001)
    with pytest.raises(ValueError, match="size limit"):
        plan_harness_migration(app, "demo/bot", skill="answer")


def test_existing_output_directory_is_not_mistaken_for_app_root(app: Path) -> None:
    parsed, args = _first_app_root_path(
        [
            str(app),
            "harness-migrate",
            "--agent",
            "demo/bot",
            "--skill",
            "answer",
            "--output",
            str(app.parent),
        ],
        DISPATCH,
    )
    assert parsed == str(app)
    assert args[-2:] == ["--output", str(app.parent)]


def test_finite_mixed_catalog_preserves_authored_app_override(app: Path) -> None:
    change(app, skills=["answer", "research"], skills_source="both")
    before = source(app).read_bytes()
    report, candidate = plan_harness_migration(app, "demo/bot")
    assert report["status"] == "candidate" and candidate is not None
    context = candidate["actions"][0]["context"]
    assert context["skill_directories"] == [
        "agents/demo/bot/skills/answer",
        "library:research",
    ]
    assert "skill_directory" not in context
    assert report["selected_skill_count"] == 2
    assert report["mappings"]["skills_source"] == "skill_directories"
    assert source(app).read_bytes() == before


def test_repeated_cli_skill_arguments_do_not_narrow_configured_catalog(
    app: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    change(app, skills=["answer", "research"], skills_source="both")
    output = app / "catalog.yaml"
    args = ["--agent", "demo/bot", "--skill", "answer", "--output", str(output)]
    assert handle_harness_migrate(args, str(app)) == 2
    assert not output.exists()
    assert handle_harness_migrate([*args, "--skill", "research"], str(app)) == 0
    candidate = yaml.safe_load(output.read_text())
    assert len(candidate["actions"][0]["context"]["skill_directories"]) == 2
    assert "secret-example-value" not in capsys.readouterr().out


@pytest.mark.parametrize(
    "selector", ["-all", ["ans*"], [], ["answer", "answer"], ["answer", 1]]
)
def test_dynamic_or_invalid_skill_selection_is_not_silently_replaced(
    app: Path, selector: Any
) -> None:
    change(app, skills=selector)
    report, candidate = plan_harness_migration(app, "demo/bot")
    assert candidate is None and report["status"] == "blocked"


def test_app_alias_cannot_fall_back_to_a_less_restrictive_library_skill(
    app: Path,
) -> None:
    directory = app / "agents/demo/bot/skills/renamed-research"
    directory.mkdir()
    (directory / "SKILL.md").write_text(
        "---\nname: research\ndescription: An app override\nallowed-tools: []\n---\nPrivate restrictions\n"
    )
    change(app, skills=["research"], skills_source="both")
    report, candidate = plan_harness_migration(app, "demo/bot")
    assert candidate is None
    assert any("format/name/spec" in reason for reason in report["blockers"])
    assert "Private restrictions" not in json.dumps(report)


def test_source_selection_does_not_leak_app_override_into_library_mode(
    app: Path,
) -> None:
    change(app, skills=["answer"], skills_source="library")
    report, candidate = plan_harness_migration(app, "demo/bot")
    assert report["status"] == "candidate"
    assert candidate["actions"][0]["context"]["skill_directories"] == ["library:answer"]


@pytest.mark.parametrize(
    "key,value",
    [
        ("channel_overrides", {"web": {"skill_only_tools": ["pay__*"]}}),
        ("skill_only_tools", ["pay__*"]),
        ("pinned_tools", ["write__save"]),
        ("vision", True),
        ("lock_active_flow", True),
        ("tool_call_timeout", 30),
    ],
)
def test_active_unqualified_policy_is_never_retired(
    app: Path, key: str, value: Any
) -> None:
    change(app, **{key: value})
    report, candidate = plan_harness_migration(app, "demo/bot", skill="answer")
    assert candidate is None and any(key in reason for reason in report["blockers"])


def test_engine_heuristics_retire_explicitly_without_leaking_values_or_active_caps(
    app: Path,
) -> None:
    change(
        app,
        light_model="secret-example-value",
        light_model_action_type="OpenAILanguageModelAction",
        light_model_temperature=0.1,
        light_model_max_tokens=1024,
        enable_transient_ack=True,
        ack_on_first_tool_call=True,
        first_emit_timeout_ms=1000,
        ack_interval_ms=3000,
        ack_statements=["secret-example-value"],
        history_limit=10,
        lean_tool_threshold=15,
        lean_presurface_k=5,
        stream_internal_progress=True,
        pilot_max_output_tokens=20000,
        pinned_tools=[],
        skill_only_tools=[],
        channel_overrides={},
        vision=False,
        max_statement_length=700,
    )
    report, candidate = plan_harness_migration(app, "demo/bot", skill="answer")
    assert candidate is not None and report["status"] == "candidate"
    target = candidate["actions"][0]["context"]
    assert target["max_requests"] == 12 and target["max_run_cost_usd"] == "0.25"
    assert target["model"] == "openai:gpt-4.1"
    assert (
        "max_output_tokens" not in target
    )  # Inactive pilot limit was not active policy.
    assert "700 characters" in target["instructions"]
    assert (
        "light_model" in report["retired_settings"]
        and "history_limit" in report["retired_settings"]
    )
    assert "pilot_max_output_tokens" in report["retired_settings"]
    assert not set(report["retired_settings"]).intersection(target)
    assert "secret-example-value" not in json.dumps(report)


@pytest.mark.parametrize(
    "settings",
    [
        {"history_limit": -1},
        {"ack_interval_ms": True},
        {"enable_transient_ack": "yes"},
        {"light_model_temperature": float("inf")},
        {"ack_statements": [1]},
        {"vision": 0},
        {"skill_only_tools": ""},
    ],
)
def test_retirement_requires_valid_typed_values(
    app: Path, settings: dict[str, Any]
) -> None:
    change(app, **settings)
    report, candidate = plan_harness_migration(app, "demo/bot", skill="answer")
    assert report["status"] == "blocked" and candidate is None


def test_app_skill_symlink_cannot_redirect_catalog_outside_agent(app: Path) -> None:
    outside = app / "outside"
    outside.mkdir()
    (outside / "SKILL.md").write_text(
        "---\nname: research\ndescription: Foreign\n---\nForeign body\n"
    )
    (app / "agents/demo/bot/skills/research").symlink_to(
        outside, target_is_directory=True
    )
    change(app, skills=["answer", "research"], skills_source="both")
    report, candidate = plan_harness_migration(app, "demo/bot")
    assert report["status"] == "blocked" and candidate is None


def test_large_inactive_numbers_do_not_overflow_during_retirement(app: Path) -> None:
    change(app, light_model_temperature=10**400, pilot_max_runtime_seconds=10**400)
    report, candidate = plan_harness_migration(app, "demo/bot", skill="answer")
    assert report["status"] == "candidate" and candidate is not None
    assert candidate["actions"][0]["context"]["max_requests"] == 12
    assert "light_model_temperature" in report["retired_settings"]
    change(app, tool_call_timeout=10**400)
    report, candidate = plan_harness_migration(app, "demo/bot", skill="answer")
    assert candidate is None and report["status"] == "blocked"


@pytest.mark.parametrize(
    "field,value",
    [
        ("allowed-groups", ["private-group"]),
        ("access-action", "private-action"),
        ("requires-tasks", [{"when": "private-condition"}]),
        ("allowed-tools", "lookup*"),
    ],
)
def test_skill_format_preflight_does_not_discard_unsupported_jv_policy(
    app: Path,
    field: str,
    value: Any,
) -> None:
    asset = app / "agents/demo/bot/skills/answer/SKILL.md"
    asset.write_text(
        "---\n"
        + yaml.safe_dump({"name": "answer", "description": "Answer", field: value})
        + "---\nAnswer.\n"
    )
    report, candidate = plan_harness_migration(app, "demo/bot", skill="answer")
    assert report["status"] == "blocked" and candidate is None
    assert "private-group" not in json.dumps(report)


def test_state_snapshot_blocker_prevents_candidate_without_exposing_source(
    app: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from tests.cli.test_harness_migration_state import state_snapshot

    data = state_snapshot()
    data["conversation"]["context"]["context"] = {}
    state = tmp_path / "private-state.json"
    state.write_text(json.dumps(data))
    output = tmp_path / "candidate.yaml"
    assert (
        handle_harness_migrate(
            [
                "--agent",
                "demo/bot",
                "--skill",
                "answer",
                "--state-snapshot",
                str(state),
                "--output",
                str(output),
            ],
            str(app),
        )
        == 2
    )
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "blocked" and not output.exists()
    assert "missing_lifetime_spend_baseline" in result["state_preflight"]["blockers"]
    assert "private user message" not in json.dumps(result)


def test_state_snapshot_is_a_flag_value_not_an_app_directory(tmp_path: Path) -> None:
    directory = tmp_path / "private-state-directory"
    directory.mkdir()
    app_root, args = _first_app_root_path(
        ["harness-migrate", "--state-snapshot", str(directory), "--agent", "demo/bot"],
        DISPATCH,
    )
    assert app_root is None
    assert args[2] == str(directory)


@pytest.mark.parametrize("enabled", [True, False])
def test_planning_maps_to_official_capability_without_legacy_state_claim(
    app: Path, enabled: bool
) -> None:
    change(app, planning=enabled)
    report, candidate = plan_harness_migration(app, "demo/bot", skill="answer")
    assert candidate is not None
    assert candidate["actions"][0]["context"]["planning"] is enabled
    assert report["mappings"]["planning"] == "planning"
    if enabled:
        assert any(
            "retained legacy plan tasks" in text for text in report["required_review"]
        )


@pytest.mark.parametrize("value", [1, "true", {}, None])
def test_planning_migration_rejects_untyped_controls(app: Path, value: Any) -> None:
    change(app, planning=value)
    report, candidate = plan_harness_migration(app, "demo/bot", skill="answer")
    assert candidate is None and any(
        "planning requires a boolean" in text for text in report["blockers"]
    )
