"""Incomplete or mismatched measurements cannot become qualification evidence."""

from copy import deepcopy
from typing import Any

import pytest

from scripts.report_harness_workload import build_report, check_case

Inputs = tuple[dict[str, Any], dict[str, list[dict[str, Any]]], list[dict[str, Any]]]


@pytest.fixture
def inputs() -> Inputs:
    case = {
        "id": "chat-exact",
        "prompt": "Reply READY",
        "expected": {"exact_text": "READY"},
    }
    manifest = {
        "cases": [case],
        "replicates": 3,
        "measured_turns_per_implementation": 3,
    }
    rows = [
        {
            "id": "turn",
            "context": {
                "utterance": "Reply READY",
                "response": "READY",
                "emitted": True,
            },
        }
    ]
    observations = [
        {
            "implementation": "native",
            "case_id": "chat-exact",
            "replicate": 1,
            "interaction_id": "turn",
            "visible": "READY",
        }
    ]
    return manifest, {"native": rows}, observations


def test_partial_sample_preserves_missing_denominator_and_unknown_usage(
    inputs: Inputs,
) -> None:
    report = build_report(*inputs)
    native = report["implementations"]["native"]
    assert native["required_measured_turns"] == 3
    assert native["measured_turns"] == 1
    assert native["missing_turns"] == 2
    assert native["missing_call_count_turns"] == 1
    assert native["missing_estimated_cost_turns"] == 1
    assert native["missing_unknown_cost_reporting_turns"] == 1
    assert native["reported_unknown_cost_events"] is None
    assert native["tokens_all_observed"] is None
    assert not native["full_gate_complete"]
    assert not report["optimization_gate_evaluable"]


def test_unpriced_attempt_is_visible_in_the_summary(inputs: Inputs) -> None:
    manifest, exports, observations = inputs
    exports["native"][0]["context"]["usage"] = {
        "estimated_cost_usd": 0.0,
        "unknown_cost_call_count": 1,
    }
    native = build_report(manifest, exports, observations)["implementations"]["native"]
    assert native["known_estimated_usd"] == 0.0
    assert native["reported_unknown_cost_events"] == 1
    assert not native["full_gate_complete"]


@pytest.mark.parametrize(
    "problem", ["duplicate", "unobserved", "prompt", "unknown-label"]
)
def test_bad_evidence_is_not_silently_counted(inputs: Inputs, problem: str) -> None:
    manifest, exports, observations = deepcopy(inputs)
    if problem == "duplicate":
        observations.append(dict(observations[0]))
    elif problem == "unobserved":
        observations.clear()
    elif problem == "prompt":
        exports["native"][0]["context"]["utterance"] = "different request"
    else:
        observations[0]["implementation"] = "typo"
    with pytest.raises(ValueError):
        build_report(manifest, exports, observations)


def test_markdown_wrapped_json_remains_a_format_failure() -> None:
    case = {"id": "json-format", "expected": {"json": {"label": "CEDAR-47"}}}
    assert not check_case(
        case, {"emitted": True, "response": '```json\n{"label":"CEDAR-47"}\n```'}
    )
    assert check_case(case, {"emitted": True, "response": '{"label":"CEDAR-47"}'})


@pytest.mark.parametrize(
    "response",
    [
        "The budget has not been provided.",
        "The budget has not been provided, but use $100.",
    ],
)
def test_missing_information_language_is_not_semantic_certification(
    response: str,
) -> None:
    case = {"id": "missing-fact", "expected": {"no_invented_budget": True}}
    assert check_case(case, {"emitted": True, "response": response}) is None


def test_fixture_setup_keeps_credentials_private_and_ignores_other_env_values(
    tmp_path: Any,
) -> None:
    import yaml
    from dotenv import dotenv_values

    from scripts.setup_conversation_workload import create_fixture

    key_env = tmp_path / "keys.env"
    key_env.write_text("OPENAI_API_KEY=synthetic-provider-key\nUNRELATED=discard\n")
    dsn_file = tmp_path / "dsn"
    dsn_file.write_text("postgresql://fixture@localhost/disposable")
    root = tmp_path / "app"
    create_fixture(root, "native", key_env, dsn_file)
    values = dotenv_values(root / ".env")
    assert values["OPENAI_API_KEY"] == "synthetic-provider-key"
    assert "UNRELATED" not in values
    assert (root / ".env").stat().st_mode & 0o777 == 0o600
    agent = yaml.safe_load((root / "agents/jvagent/bench/agent.yaml").read_text())
    assert agent["actions"][0]["context"]["model_settings"]["max_tokens"] == 8192
    assert "__APP_ROOT__" not in str(agent)


def test_fixture_setup_refuses_to_overwrite_an_existing_app(tmp_path: Any) -> None:
    from scripts.setup_conversation_workload import create_fixture

    key_env = tmp_path / "keys.env"
    key_env.write_text("OPENAI_API_KEY=synthetic-provider-key\n")
    dsn_file = tmp_path / "dsn"
    dsn_file.write_text("postgresql://fixture@localhost/disposable")
    root = tmp_path / "app"
    root.mkdir()
    original = root / "app.yaml"
    original.write_text("preserve this app\n")
    with pytest.raises(FileExistsError):
        create_fixture(root, "native", key_env, dsn_file)
    assert original.read_text() == "preserve this app\n"
