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
    "policy",
    ["missing", "wrong", "valid", "changed", "disabled", "missing_action", "malformed"],
)
def test_interview_hook_migration_requires_host_review_without_import(
    app: Path, policy: str
) -> None:
    from jvagent.action.interview.native_hooks import native_hook_digest

    directory = app / "agents/demo/bot/skills/answer"
    (directory / "scripts").mkdir()
    module = directory / "scripts/custom_tools.py"
    module.write_text(
        "raise RuntimeError('MUST_NOT_IMPORT')\nasync def check(ctx): return ctx.valid(value=ctx.value)\n"
    )
    (directory / "SKILL.md").write_text(
        "---\n"
        + yaml.safe_dump(
            {
                "name": "answer",
                "description": "Interview",
                "trust_tier": "trusted",
                "extends": "action:jvagent/interview",
                "interview": {
                    "fields": [{"key": "name", "prompt": "Name?", "validator": "check"}]
                },
            }
        )
        + "---\nUse interview tools.\n"
    )
    context: dict[str, Any] = {"enabled": policy != "disabled"}
    if policy in {"valid", "changed", "disabled"}:
        context["native_hook_digests"] = {"answer": native_hook_digest(directory)}
    elif policy == "wrong":
        context["native_hook_digests"] = {"answer": "sha256:" + "0" * 64}
    elif policy == "malformed":
        context["native_hook_digests"] = []
    if policy == "changed":
        module.write_text(module.read_text() + "\n# changed after review\n")
    document = yaml.safe_load(source(app).read_text())
    document["actions"][0]["context"].update(skills=["answer"], skills_source="app")
    if policy != "missing_action":
        document["actions"].append({"action": "jvagent/interview", "context": context})
    source(app).write_text(yaml.safe_dump(document))
    before, code = source(app).read_bytes(), module.read_bytes()
    report, candidate = plan_harness_migration(app, "demo/bot")
    assert (candidate is not None) == (policy == "valid")
    assert any(
        "reviewed native interview policy" in item for item in report["blockers"]
    ) == (policy != "valid")
    assert report["selected_skill_count"] == 1
    assert source(app).read_bytes() == before and module.read_bytes() == code
    assert "MUST_NOT_IMPORT" not in str(report)
    assert "secret-example-value" not in str(report)


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


@pytest.mark.parametrize("enabled", [False, True])
def test_reasoning_visibility_is_explicitly_preserved(app: Path, enabled: bool) -> None:
    change(app, stream_reasoning_trace=enabled)
    before = source(app).read_bytes()
    report, candidate = plan_harness_migration(app, "demo/bot", skill="answer")
    assert candidate is not None and not report["blockers"]
    assert candidate["actions"][0]["context"]["publish_reasoning"] is enabled
    assert report["mappings"]["stream_reasoning_trace"] == "publish_reasoning"
    assert source(app).read_bytes() == before


@pytest.mark.parametrize("invalid", [0, 1, "true", None, [], {}])
def test_reasoning_visibility_rejects_coercion(app: Path, invalid: Any) -> None:
    change(app, stream_reasoning_trace=invalid)
    report, candidate = plan_harness_migration(app, "demo/bot", skill="answer")
    assert candidate is None
    assert "stream_reasoning_trace requires a boolean" in report["blockers"]


@pytest.mark.parametrize("tier", ["minimal", "standard", "full"])
def test_tool_tier_becomes_explicit_clock_service(app: Path, tier: str) -> None:
    change(app, tool_tier=tier)
    before = source(app).read_bytes()
    report, candidate = plan_harness_migration(app, "demo/bot", skill="answer")
    assert candidate is not None and not report["blockers"]
    assert "tool_tier" not in candidate["actions"][0]["context"]
    utilities = [
        a for a in candidate["actions"] if a["action"] == "jvagent/agent_utils"
    ]
    assert bool(utilities) == (tier != "minimal")
    assert report["mappings"]["tool_tier"] == "declared clock service"
    assert source(app).read_bytes() == before


@pytest.mark.parametrize(
    "tier,enabled,accepted",
    [
        ("standard", True, True),
        ("standard", False, False),
        ("full", True, True),
        ("minimal", False, True),
        ("minimal", True, False),
        ("standard", 0, False),
        ("standard", "false", False),
    ],
)
def test_clock_migration_preserves_existing_enablement(
    app: Path, tier: str, enabled: Any, accepted: bool
) -> None:
    change(app, tool_tier=tier)
    doc = yaml.safe_load(source(app).read_text())
    declaration = {
        "action": "jvagent/agent_utils",
        "context": {"enabled": enabled, "description": "Keep this declaration"},
    }
    doc["actions"].append(declaration)
    source(app).write_text(yaml.safe_dump(doc))
    before = source(app).read_bytes()
    report, candidate = plan_harness_migration(app, "demo/bot", skill="answer")
    assert (candidate is not None) == accepted
    if candidate is not None:
        assert [
            a for a in candidate["actions"] if a["action"] == "jvagent/agent_utils"
        ] == [declaration]
        assert not report.get("added_actions")
    assert source(app).read_bytes() == before


@pytest.mark.parametrize("tier", [None, False, 1, "", "private-invalid-tier"])
def test_unknown_tool_tier_is_not_guessed(app: Path, tier: Any) -> None:
    change(app, tool_tier=tier)
    report, candidate = plan_harness_migration(app, "demo/bot", skill="answer")
    assert candidate is None
    assert any("Core tool tier" in b for b in report["blockers"])
    assert "private-invalid-tier" not in json.dumps(report)


@pytest.mark.parametrize("block_raw", [True, False])
def test_native_tool_controls_retire_without_changing_host_policy(
    app: Path, block_raw: bool
) -> None:
    change(app, block_raw_tool_invocation=block_raw, tool_servers="-all")
    before = source(app).read_bytes()
    original = yaml.safe_load(before)
    report, candidate = plan_harness_migration(app, "demo/bot", skill="answer")
    assert candidate is not None and not report["blockers"]
    target = candidate["actions"][0]["context"]
    assert "block_raw_tool_invocation" not in target and "tool_servers" not in target
    assert set(report["retired_settings"]) >= {
        "block_raw_tool_invocation",
        "tool_servers",
    }
    assert "permission" in report["retired_settings"]["block_raw_tool_invocation"]
    assert candidate["actions"][1:-1] == original["actions"][1:]
    assert candidate["actions"][-1] == {
        "action": "jvagent/agent_utils",
        "context": {"enabled": True},
    }
    assert report["added_actions"] == ["jvagent/agent_utils"]
    assert source(app).read_bytes() == before


@pytest.mark.parametrize(
    "selector",
    ["records", ["MCPAction"], [], None, False, {}, "private-selector-value"],
)
def test_restricted_mcp_selector_cannot_be_silently_widened(
    app: Path, selector: Any
) -> None:
    change(app, tool_servers=selector)
    report, candidate = plan_harness_migration(app, "demo/bot", skill="answer")
    assert candidate is None
    assert any("MCP selector" in item for item in report["blockers"])
    assert "private-selector-value" not in json.dumps(report)


@pytest.mark.parametrize("value", ["true", 1, None])
def test_raw_tool_control_retirement_requires_an_actual_boolean(
    app: Path, value: Any
) -> None:
    change(app, block_raw_tool_invocation=value)
    report, candidate = plan_harness_migration(app, "demo/bot", skill="answer")
    assert candidate is None
    assert any("Invalid legacy setting" in item for item in report["blockers"])


@pytest.mark.parametrize(
    "reviewed", [None, [], ["*"], [""], [1], ["mcp_records__lookup_record"]]
)
@pytest.mark.parametrize("explicit_selector", [False, True])
def test_all_mcp_migration_requires_reviewed_native_tools(
    app: Path, reviewed: Any, explicit_selector: bool
) -> None:
    if explicit_selector:
        change(app, tool_servers="-all")
    document = yaml.safe_load(source(app).read_text())
    configuration = {
        "enabled": True,
        "servers": [{"name": "records", "transport": "stdio", "command": "python"}],
    }
    if reviewed is not None:
        configuration["native_read_tools"] = reviewed
    document["actions"].append({"action": "jvagent/mcp", "context": configuration})
    source(app).write_text(yaml.safe_dump(document))
    before = source(app).read_bytes()
    report, candidate = plan_harness_migration(app, "demo/bot", skill="answer")
    accepted = reviewed == ["mcp_records__lookup_record"]
    assert (candidate is not None) == accepted
    if accepted:
        assert (
            next(a for a in candidate["actions"] if a["action"] == "jvagent/mcp")
            == document["actions"][-1]
        )
    else:
        assert any("reviewed native_read_tools" in item for item in report["blockers"])
    assert source(app).read_bytes() == before


@pytest.mark.parametrize("enabled", [False, 0, "false"])
def test_only_explicitly_disabled_mcp_skips_native_review(
    app: Path, enabled: Any
) -> None:
    document = yaml.safe_load(source(app).read_text())
    document["actions"].append(
        {"action": "jvagent/mcp", "context": {"enabled": enabled}}
    )
    source(app).write_text(yaml.safe_dump(document))
    report, candidate = plan_harness_migration(app, "demo/bot", skill="answer")
    assert (candidate is not None) == (enabled is False)
    if candidate is None:
        assert any("reviewed native_read_tools" in item for item in report["blockers"])


@pytest.mark.parametrize("width", [0, 1])
def test_serial_tool_ceiling_uses_public_sdk_execution_mode(
    app: Path, width: int
) -> None:
    change(app, max_concurrent_tools=width)
    before = source(app).read_bytes()
    report, candidate = plan_harness_migration(app, "demo/bot", skill="answer")
    assert candidate is not None and not report["blockers"]
    assert candidate["actions"][0]["context"]["tool_execution_mode"] == "sequential"
    assert report["mappings"]["max_concurrent_tools"] == "tool_execution_mode"
    assert source(app).read_bytes() == before


@pytest.mark.parametrize("width", [-1, True, "1", 1.0, None, 2, 10])
def test_unqualified_tool_width_cannot_silently_expand(app: Path, width: Any) -> None:
    change(app, max_concurrent_tools=width)
    before = source(app).read_bytes()
    report, candidate = plan_harness_migration(app, "demo/bot", skill="answer")
    assert candidate is None
    assert any("tool concurrency" in blocker for blocker in report["blockers"])
    assert source(app).read_bytes() == before


def test_candidate_preserves_modules_identity_assets_and_limits(app: Path) -> None:
    before = source(app).read_bytes()
    original = yaml.safe_load(before)
    report, candidate = plan_harness_migration(app, "demo/bot", skill="answer")
    assert report["status"] == "candidate"
    assert report["blockers"] == []
    assert candidate is not None
    target = candidate["actions"][0]
    assert target["action"] == "jvagent/native_harness"
    assert candidate["actions"][1:-1] == original["actions"][1:]
    assert candidate["actions"][-1] == {
        "action": "jvagent/agent_utils",
        "context": {"enabled": True},
    }
    assert report["added_actions"] == ["jvagent/agent_utils"]
    assert candidate["context"] == original["context"]
    context = target["context"]
    assert context["model"] == "openai:gpt-4.1"
    assert context["tool_execution_mode"] == "sequential"
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
        "jvagent/agent_utils",
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
        ("vision", True),
    ],
)
def test_active_unqualified_policy_is_never_retired(
    app: Path, key: str, value: Any
) -> None:
    change(app, **{key: value})
    report, candidate = plan_harness_migration(app, "demo/bot", skill="answer")
    assert candidate is None and any(key in reason for reason in report["blockers"])


def test_channel_policy_migrates_without_writing_or_reporting_instructions(
    app: Path,
) -> None:
    change(
        app,
        channel_overrides={
            "voice": {
                "system_prompt_extra": "private-channel-instruction",
                "max_statement_length": 280,
                "activation_budget": 0,
                "max_duration_seconds": 30,
                "max_turn_cost_usd": "0.10",
                "history_limit": 6,
                "ack_statements": ["private-channel-instruction"],
                "pinned_tools": [],
                "with_event": False,
                "tool_call_timeout": 0,
            }
        },
    )
    before = source(app).read_bytes()
    report, candidate = plan_harness_migration(app, "demo/bot", skill="answer")
    assert candidate is not None and source(app).read_bytes() == before
    target = candidate["actions"][0]["context"]
    assert target["channel_limits"] == {
        "voice": {"max_requests": 1, "timeout_seconds": 30, "max_run_cost_usd": "0.10"}
    }
    assert target["channel_instructions"] == {
        "voice": "private-channel-instruction\n\nKeep final replies within 280 characters when practical."
    }
    assert "channel_overrides" not in target
    assert "private-channel-instruction" not in json.dumps(report)
    assert set(report["retired_channel_settings"]) == {
        "history_limit",
        "ack_statements",
        "pinned_tools",
        "with_event",
        "tool_call_timeout",
    }


@pytest.mark.parametrize(
    "declaration",
    [
        {"activation_budget": 13},
        {"max_duration_seconds": 61},
        {"max_turn_cost_usd": "0.26"},
        {"max_turn_cost_usd": True},
        {"max_duration_seconds": 0},
        {"max_duration_seconds": float("inf")},
        {"tool_call_timeout": "private-channel-instruction"},
        {"tool_call_timeout": False},
        {"with_event": 0},
        {"with_event": True},
        {"denied_tools": ["private-channel-instruction"]},
        {"pinned_tools": [True]},
        {"history_limit": True},
        {"history_limit": 10**400},
        {"system_prompt_extra": {"private-channel-instruction": 1}},
        {"unknown-private-key": "private-channel-instruction"},
        {"max_statement_length": -1},
    ],
)
def test_channel_policy_refuses_unqualified_controls_atomically(
    app: Path, declaration: Any
) -> None:
    change(
        app,
        channel_overrides={
            "default": {"system_prompt_extra": "valid"},
            "voice": declaration,
        },
    )
    before = source(app).read_bytes()
    report, candidate = plan_harness_migration(app, "demo/bot", skill="answer")
    assert candidate is None and source(app).read_bytes() == before
    assert any("channel_overrides" in value for value in report["blockers"])
    assert "private-channel-instruction" not in json.dumps(report)
    assert "unknown-private-key" not in json.dumps(report)
    assert report["blocked_channel_settings"] == [
        "unknown" if "unknown-private-key" in declaration else next(iter(declaration))
    ]


@pytest.mark.parametrize(
    "profiles", [None, [], {" voice": {}}, {"": {}}, {2: {}}, {"voice": []}]
)
def test_invalid_channel_profile_shapes_block(app: Path, profiles: Any) -> None:
    change(app, channel_overrides=profiles)
    report, candidate = plan_harness_migration(app, "demo/bot", skill="answer")
    assert candidate is None and any(
        "channel_overrides" in value for value in report["blockers"]
    )


def test_invalid_global_limit_with_channel_policy_returns_report(app: Path) -> None:
    change(
        app,
        activation_budget="bad",
        channel_overrides={"voice": {"activation_budget": 3}},
    )
    report, candidate = plan_harness_migration(app, "demo/bot", skill="answer")
    assert candidate is None and report["blockers"]


@pytest.mark.parametrize("pins", [[], ["write__save"], ["write__*", "*", "read__?"]])
def test_tool_pins_retire_without_creating_native_tool_authority(
    app: Path, pins: Any
) -> None:
    change(app, pinned_tools=pins, channel_overrides={"web": {"pinned_tools": pins}})
    before = source(app).read_bytes()
    report, candidate = plan_harness_migration(app, "demo/bot", skill="answer")
    assert candidate is not None and source(app).read_bytes() == before
    target = candidate["actions"][0]["context"]
    assert "pinned_tools" in report["retired_settings"]
    assert "pinned_tools" in report["retired_channel_settings"]
    assert "pinned_tools" not in target and "channel_overrides" not in target
    assert "channel_instructions" not in target and "channel_limits" not in target
    assert "write__" not in json.dumps(report)
    assert "write__" not in target["instructions"]
    assert any("host permissions" in text for text in report["required_review"])


@pytest.mark.parametrize(
    "pins", [None, "read__*", {}, [None], [True], [""], [" read__x"], ["read__x "]]
)
@pytest.mark.parametrize("channel", [False, True])
def test_invalid_pin_declarations_never_silently_retire(
    app: Path, pins: Any, channel: bool
) -> None:
    change(
        app,
        **(
            {"channel_overrides": {"web": {"pinned_tools": pins}}}
            if channel
            else {"pinned_tools": pins}
        ),
    )
    report, candidate = plan_harness_migration(app, "demo/bot", skill="answer")
    assert candidate is None and report["blockers"]


@pytest.mark.parametrize("restriction", ["denied_tools", "skill_only_tools"])
def test_retired_pins_do_not_erase_active_tool_restrictions(
    app: Path, restriction: str
) -> None:
    change(
        app,
        channel_overrides={
            "web": {"pinned_tools": ["*"], restriction: ["write__save"]}
        },
    )
    report, candidate = plan_harness_migration(app, "demo/bot", skill="answer")
    assert candidate is None and restriction in report["blocked_channel_settings"]


@pytest.mark.parametrize(
    "base,profile,expected", [(0, 15, 15), (30, 15, 15), (30, 30, 30), (0, 0, None)]
)
def test_tool_deadlines_migrate_to_native_sdk_clocks(
    app: Path, base: Any, profile: Any, expected: Any
) -> None:
    change(
        app,
        tool_call_timeout=base,
        channel_overrides={"voice": {"tool_call_timeout": profile}},
    )
    report, candidate = plan_harness_migration(app, "demo/bot", skill="answer")
    assert candidate is not None
    target = candidate["actions"][0]["context"]
    assert target.get("tool_timeout_seconds") == (base or None)
    assert (
        target.get("channel_limits", {}).get("voice", {}).get("tool_timeout_seconds")
        == expected
    )
    assert "tool_call_timeout" not in target
    assert any("SDK" in text and "clock" in text for text in report["required_review"])


@pytest.mark.parametrize(
    "base,profile",
    [
        (15, 30),
        (15, 0),
        (15, None),
        (0, True),
        (0, "private-deadline"),
        (float("inf"), 15),
        (None, 15),
    ],
)
def test_tool_deadline_migration_never_widens_or_disables_an_active_base(
    app: Path, base: Any, profile: Any
) -> None:
    change(
        app,
        tool_call_timeout=base,
        channel_overrides={"voice": {"tool_call_timeout": profile}},
    )
    report, candidate = plan_harness_migration(app, "demo/bot", skill="answer")
    assert candidate is None and report["blockers"]
    assert "private-deadline" not in json.dumps(report)


@pytest.mark.parametrize("lock_setting", [None, False, True])
def test_turn_lock_retires_into_skill_continuation_without_changing_limits(
    app: Path,
    lock_setting: bool | None,
) -> None:
    if lock_setting is not None:
        change(app, lock_active_flow=lock_setting)
    original = source(app).read_bytes()
    report, candidate = plan_harness_migration(app, "demo/bot", skill="answer")
    assert candidate is not None
    native = candidate["actions"][0]["context"]
    assert "lock_active_flow" not in native
    assert report["mappings"]["lock_active_flow"] == "retired"
    assert "domain task state" in report["retired_settings"]["lock_active_flow"]
    assert native["max_requests"] == 12
    assert native["max_run_cost_usd"] == "0.25"
    assert "lock_active_flow" not in native["instructions"]
    assert source(app).read_bytes() == original


@pytest.mark.parametrize("invalid", [None, 0, 1, "true", [], {}])
def test_turn_lock_retirement_requires_a_boolean(app: Path, invalid: Any) -> None:
    change(app, lock_active_flow=invalid)
    report, candidate = plan_harness_migration(app, "demo/bot", skill="answer")
    assert candidate is None
    assert any("lock_active_flow" in reason for reason in report["blockers"])


@pytest.mark.parametrize("model_service", [False, True])
def test_default_vision_migrates_to_native_images_without_second_interpreter(
    app: Path,
    model_service: bool,
) -> None:
    change(app, vision=True)
    document = yaml.safe_load(source(app).read_text())
    document["actions"] = [
        a for a in document["actions"] if a["action"] != "demo/modular_tool"
    ]
    document["actions"].append(
        {"action": "jvagent/vision", "context": {"model": "gpt-4o"}}
    )
    if model_service:
        document["actions"].append({"action": "jvagent/litellm_lm"})
    source(app).write_text(yaml.safe_dump(document))
    original = source(app).read_bytes()

    report, candidate = plan_harness_migration(app, "demo/bot", skill="answer")

    assert candidate is not None
    native = candidate["actions"][0]["context"]
    assert native["input_media_types"] == [
        "image/gif",
        "image/jpeg",
        "image/png",
        "image/webp",
    ]
    assert "vision" not in native
    assert ("jvagent/litellm_lm" in report["retired_actions"]) is model_service
    assert report["preserved_action_count"] == 1
    assert "jvagent/vision" in report["retired_actions"]
    assert all(a["action"] != "jvagent/vision" for a in candidate["actions"])
    assert "vision" in report["retired_settings"]
    assert source(app).read_bytes() == original
    assert "secret-example-value" not in json.dumps(report)


def test_unknown_action_caller_blocks_vision_retirement(app: Path) -> None:
    change(app, vision=True)
    document = yaml.safe_load(source(app).read_text())
    document["actions"].append({"action": "jvagent/vision"})
    source(app).write_text(yaml.safe_dump(document))
    report, candidate = plan_harness_migration(app, "demo/bot", skill="answer")
    assert candidate is None
    assert any("vision" in reason.lower() for reason in report["blockers"])


@pytest.mark.parametrize(
    "vision_context",
    [
        {"enabled": False},
        {"enabled": 1},
        {"model": "other-model"},
        {"model": {}},
        {"model": []},
        {"model_action_type": "OtherProvider"},
        {"interpretation_prompt": "secret-example-value"},
        {"model_max_tokens": 50},
        {"api_key": "secret-example-value"},
    ],
)
def test_unreviewed_vision_configuration_blocks_retirement(
    app: Path, vision_context: dict[str, Any]
) -> None:
    change(app, vision=True)
    document = yaml.safe_load(source(app).read_text())
    document["actions"] = [
        a for a in document["actions"] if a["action"] != "demo/modular_tool"
    ]
    document["actions"].append({"action": "jvagent/vision", "context": vision_context})
    source(app).write_text(yaml.safe_dump(document))
    report, candidate = plan_harness_migration(app, "demo/bot", skill="answer")
    assert candidate is None
    assert any("vision" in reason.lower() for reason in report["blockers"])
    assert "secret-example-value" not in json.dumps(report)


@pytest.mark.parametrize(
    "metadata",
    [
        "requires-actions: [VisionAction]",
        "allowed-tools: [interpret_images]",
        "allowed-tools: ['*']",
        "requires-tools: null",
    ],
)
def test_vision_dependent_skill_blocks_action_retirement(
    app: Path, metadata: str
) -> None:
    change(app, vision=True)
    document = yaml.safe_load(source(app).read_text())
    document["actions"] = [
        a for a in document["actions"] if a["action"] != "demo/modular_tool"
    ]
    document["actions"].append({"action": "jvagent/vision"})
    source(app).write_text(yaml.safe_dump(document))
    skill = app / "agents/demo/bot/skills/answer/SKILL.md"
    skill.write_text(
        "---\nname: answer\ndescription: Answer\n"
        + metadata
        + "\n---\nInspect images.\n"
    )
    report, candidate = plan_harness_migration(app, "demo/bot", skill="answer")
    assert candidate is None
    assert any("vision" in reason.lower() for reason in report["blockers"])


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


@pytest.mark.parametrize("active_flow", [False, True])
def test_state_snapshot_blocker_prevents_candidate_without_exposing_source(
    app: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    active_flow: bool,
) -> None:
    from tests.cli.test_harness_migration_state import state_snapshot

    data = state_snapshot()
    if active_flow:
        change(app, lock_active_flow=True)
        data["conversation"]["context"]["tasks"] = [
            {
                "id": "private-flow",
                "status": "active",
                "owner_action": "private-flow",
                "task_type": "SKILL",
                "data": {"state": "private"},
            }
        ]
    else:
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
    assert (
        "nonterminal_or_malformed_tasks"
        if active_flow
        else "missing_lifetime_spend_baseline"
    ) in result["state_preflight"]["blockers"]
    assert result["mappings"]["lock_active_flow"] == "retired"
    assert "private-flow" not in json.dumps(result)
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


def test_native_candidate_retires_only_legacy_action_model_settings(app: Path):
    declaration = yaml.safe_load(source(app).read_text())
    reply = declaration["actions"][-1]
    reply["context"].update(
        model_action_type="OpenAILanguageModelAction",
        model="private-reply-model",
        model_temperature=0.3,
        model_max_tokens=4096,
        channel_formats={"web": "Use plain text"},
    )
    declaration["actions"].append(
        {
            "action": "jvagent/mcp",
            "context": {
                "enabled": False,
                "model_action_type": "OpenAILanguageModelAction",
                "model": "private-mcp-model",
                "servers": [{"name": "fixture", "enabled": False}],
            },
        }
    )
    source(app).write_text(yaml.safe_dump(declaration))
    report, candidate = plan_harness_migration(app, "demo/bot", skill="answer")
    assert candidate is not None
    assert report["retired_action_model_settings"] == {
        "jvagent/reply": [
            "model",
            "model_action_type",
            "model_max_tokens",
            "model_temperature",
        ],
        "jvagent/mcp": ["model", "model_action_type"],
    }
    assert "private-reply-model" not in json.dumps(report)
    assert "private-mcp-model" not in json.dumps(report)
    contexts = {action["action"]: action["context"] for action in candidate["actions"]}
    assert contexts["jvagent/reply"] == {
        "enabled": True,
        "channel_formats": {"web": "Use plain text"},
    }
    assert contexts["jvagent/mcp"] == {
        "enabled": False,
        "servers": [{"name": "fixture", "enabled": False}],
    }
    assert yaml.safe_load(source(app).read_text()) == declaration


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({}, True),
        ({"model": "openai/gpt-4.1-mini"}, False),
        ({"temperature": 0.7}, False),
        ({"max_tokens": 8192.0}, False),
        ({"timeout": True}, False),
        ({"timeout": float("inf")}, False),
        ({"drop_params": True}, False),
    ],
)
def test_selected_provider_maps_only_qualified_defaults_and_timeout(
    app: Path, overrides: dict[str, Any], expected: bool
) -> None:
    document = yaml.safe_load(source(app).read_text())
    owner = document["actions"][0]
    owner["context"]["model_temperature"] = 0.2
    document["actions"] = [
        owner,
        document["actions"][2],
        {
            "action": "jvagent/litellm_lm",
            "context": {
                "model": "openai/gpt-4.1",
                "temperature": 0.2,
                "max_tokens": 8192,
                "timeout": 120,
                **overrides,
            },
        },
    ]
    source(app).write_text(yaml.safe_dump(document))
    report, candidate = plan_harness_migration(app, "demo/bot", skill="answer")
    assert (candidate is not None) is expected
    if expected:
        assert candidate is not None
        assert report["mappings"]["model_service.timeout"] == "model_settings.timeout"
        assert candidate["actions"][0]["context"]["model_settings"]["timeout"] == 120
        assert report["retired_actions"] == ["jvagent/litellm_lm"]
