"""Migration emits reviewable declarations without dropping policy or secrets."""

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

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
        "light_model",
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
