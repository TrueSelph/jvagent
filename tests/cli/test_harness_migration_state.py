"""Historical accounting and unresolved work cannot silently become zero state."""

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from jvagent.cli.harness_migration_state import inspect_state_snapshot


def state_snapshot() -> dict[str, Any]:
    return {
        "conversation": {
            "id": "conv",
            "entity": "Conversation",
            "context": {
                "user_id": "owner",
                "session_id": "session",
                "interaction_count": 1,
                "last_interaction_id": "turn",
                "context": {"_cost_usd_total": "0.25"},
                "tasks": [],
            },
        },
        "interactions": [
            {
                "id": "turn",
                "entity": "Interaction",
                "context": {
                    "conversation_id": "conv",
                    "user_id": "owner",
                    "session_id": "session",
                    "utterance": "private user message",
                    "response": "private reply",
                    "closed": True,
                    "emitted": True,
                    "completed_at": "2026-11-05T12:00:01Z",
                    "actions": ["OrchestratorInteractAction", "ReplyAction"],
                    "observability_metrics": [
                        {
                            "event_type": "model_call",
                            "data": {
                                "usage": {
                                    "prompt_tokens": 8,
                                    "completion_tokens": 2,
                                    "total_tokens": 10,
                                },
                                "cost_record": {
                                    "amount": "0.10",
                                    "currency": "USD",
                                    "estimated": True,
                                    "source": "jv_cost_estimator",
                                },
                            },
                        }
                    ],
                },
            }
        ],
    }


@pytest.fixture
def snapshot() -> dict[str, Any]:
    return state_snapshot()


def inspect(tmp_path: Path, snapshot: dict[str, Any]) -> dict[str, Any]:
    path = tmp_path / "state.json"
    path.write_text(json.dumps(snapshot))
    before = path.read_bytes()
    result = inspect_state_snapshot(path)
    assert path.read_bytes() == before
    assert "private" not in json.dumps(result)
    assert result["cutover_authorized"] is False
    return result


def test_receipts_and_larger_pruned_ledger_remain_distinct(
    tmp_path: Path, snapshot: dict[str, Any]
) -> None:
    result = inspect(tmp_path, snapshot)
    assert result["status"] == "review_required"
    assert result["verified_retained_receipts"] == {
        "requests_including_embeddings": 1,
        "tokens": 10,
        "reported_usd": "0",
        "estimated_usd": "0.10",
    }
    assert result["legacy_ledger_usd"] == "0.25"


@pytest.mark.parametrize(
    "target,key,value,blocker",
    [
        (
            "conversation",
            "tasks",
            [{"status": "parked"}],
            "nonterminal_or_malformed_tasks",
        ),
        (
            "conversation",
            "tasks",
            [{"status": "completed", "data": {"pilot_snapshot": "private"}}],
            "retained_task_effect_delivery_review",
        ),
        (
            "conversation",
            "native_spend",
            {"counter": {}},
            "existing_native_spend_requires_reconciliation",
        ),
        ("conversation", "interaction_count", 2, "retained_interaction_count_mismatch"),
        (
            "conversation",
            "interaction_count",
            True,
            "retained_interaction_count_mismatch",
        ),
        (
            "conversation",
            "last_interaction_id",
            "missing",
            "retained_frontier_mismatch",
        ),
        ("turn", "user_id", "foreign", "foreign_interaction_scope"),
        ("turn", "conversation_id", "other", "foreign_interaction_scope"),
        ("turn", "session_id", "other", "foreign_interaction_scope"),
        ("turn", "closed", False, "unsettled_interaction_or_delivery"),
        ("turn", "emitted", False, "unsettled_interaction_or_delivery"),
        (
            "turn",
            "native_run",
            {"status": "delivered"},
            "native_or_mixed_history_requires_reconciliation",
        ),
        ("turn", "response", None, "nontext_or_missing_transcript"),
        ("turn", "actions", ["SendEmailAction"], "unqualified_action_effects"),
        ("turn", "actions", [{}], "unqualified_action_effects"),
        (
            "turn",
            "directives",
            [{"content": "private"}],
            "retained_tool_or_host_state_review",
        ),
        ("turn", "observability_metrics", [], "missing_model_receipt_provenance"),
        (
            "turn",
            "observability_metrics",
            [{"event_type": "tool_call"}],
            "unqualified_attempt_tool_or_reserve_event",
        ),
        (
            "turn",
            "usage",
            {"total_tokens": 10, "model_call_count": 1, "unknown_cost_call_count": 1},
            "aggregate_receipts_require_reconciliation",
        ),
    ],
)
def test_unsafe_state_requires_reconciliation(
    tmp_path: Path,
    snapshot: dict[str, Any],
    target: str,
    key: str,
    value: Any,
    blocker: str,
) -> None:
    container = (
        snapshot["conversation"]["context"]
        if target == "conversation"
        else snapshot["interactions"][0]["context"]
    )
    container[key] = value
    assert blocker in inspect(tmp_path, snapshot)["blockers"]


@pytest.mark.parametrize("ledger", [None, False, "NaN", "-1", "0.01"])
def test_missing_or_invalid_lifetime_accounting_is_not_zero(
    tmp_path: Path, snapshot: dict[str, Any], ledger: Any
) -> None:
    context = snapshot["conversation"]["context"]["context"]
    if ledger is None:
        context.clear()
    else:
        context["_cost_usd_total"] = ledger
    assert inspect(tmp_path, snapshot)["status"] == "blocked"


@pytest.mark.parametrize(
    "field,value",
    [
        ("amount", None),
        ("amount", True),
        ("amount", "Infinity"),
        ("amount", "-1"),
        ("estimated", "false"),
        ("currency", "EUR"),
        ("source", "unavailable_pricing"),
    ],
)
def test_untrustworthy_cost_provenance_blocks(
    tmp_path: Path, snapshot: dict[str, Any], field: str, value: Any
) -> None:
    snapshot["interactions"][0]["context"]["observability_metrics"][0]["data"][
        "cost_record"
    ][field] = value
    assert inspect(tmp_path, snapshot)["status"] == "blocked"


@pytest.mark.parametrize(
    "raw",
    [
        b'{"conversation":{},"conversation":{},"interactions":[]}',
        b'{"conversation":NaN,"interactions":[]}',
        b"[]",
        b'{"private": "password"}',
    ],
)
def test_invalid_snapshot_refuses_without_echo(tmp_path: Path, raw: bytes) -> None:
    path = tmp_path / "bad.json"
    path.write_bytes(raw)
    with pytest.raises(ValueError) as error:
        inspect_state_snapshot(path)
    assert "password" not in str(error.value)


def test_duplicate_and_oversized_interaction_sets_refuse(
    tmp_path: Path, snapshot: dict[str, Any]
) -> None:
    snapshot["interactions"] *= 2
    with pytest.raises(ValueError, match="duplicate"):
        inspect(tmp_path, snapshot)
    snapshot["interactions"] = [
        copy.deepcopy(snapshot["interactions"][0]) for _ in range(65)
    ]
    with pytest.raises(ValueError, match="oversized"):
        inspect(tmp_path, snapshot)


@pytest.mark.parametrize("estimated", [True, False])
def test_explicit_free_receipt_differs_from_a_zero_estimate(
    tmp_path: Path, snapshot: dict[str, Any], estimated: bool
) -> None:
    cost = snapshot["interactions"][0]["context"]["observability_metrics"][0]["data"][
        "cost_record"
    ]
    cost.update(
        amount="0",
        estimated=estimated,
        source="jv_cost_estimator" if estimated else "provider_reported",
    )
    result = inspect(tmp_path, snapshot)
    assert ("zero_estimate_requires_reconciliation" in result["blockers"]) is estimated
    assert result["cutover_authorized"] is False


def test_file_and_byte_bounds_refuse_before_parsing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import jvagent.cli.harness_migration_state as module

    with pytest.raises(ValueError, match="regular file"):
        inspect_state_snapshot(tmp_path)
    path = tmp_path / "oversized.json"
    path.write_bytes(b"{" * 20)
    monkeypatch.setattr(module, "MAX_STATE_BYTES", 10)
    with pytest.raises(ValueError, match="size limit"):
        inspect_state_snapshot(path)


@pytest.mark.parametrize("field", ["task_status", "event_type"])
def test_structured_discriminators_do_not_escape_redacted_preflight(
    tmp_path: Path, snapshot: dict[str, Any], field: str
) -> None:
    if field == "task_status":
        snapshot["conversation"]["context"]["tasks"] = [
            {"status": {"private": "value"}}
        ]
    else:
        snapshot["interactions"][0]["context"]["observability_metrics"] = [
            {"event_type": {"private": "value"}}
        ]
    assert inspect(tmp_path, snapshot)["status"] == "blocked"
