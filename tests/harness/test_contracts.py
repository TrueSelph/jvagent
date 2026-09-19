"""HP-00: NativeCaller, TurnRun transitions, snapshot, payload authority."""

from __future__ import annotations

import pytest

from jvagent.harness.contracts import (
    FORBIDDEN_HOST_DOMAIN_KEYS,
    HarnessContractError,
    IdempotencyClass,
    NativeCaller,
    ToolSurfaceSnapshot,
    TurnRunState,
    assert_turn_run_transition,
    host_work_execution_context,
    native_caller_from_mapping,
    reject_host_domain_fields,
    reject_model_authority_fields,
)


def test_native_caller_fields_are_only_agent_user_session():
    caller = NativeCaller(
        agent_id="agent-1",
        user_id="user-1",
        session_id="sess-1",
    )
    assert caller.as_tuple() == ("agent-1", "user-1", "sess-1")
    assert set(caller.to_mapping()) == {"agent_id", "user_id", "session_id"}


def test_native_caller_from_mapping_rejects_workspace_id():
    with pytest.raises(HarnessContractError, match="host-domain"):
        native_caller_from_mapping(
            {
                "agent_id": "a",
                "user_id": "u",
                "session_id": "s",
                "workspace_id": "ws-1",
            }
        )


@pytest.mark.parametrize("key", sorted(FORBIDDEN_HOST_DOMAIN_KEYS))
def test_host_domain_keys_are_rejected(key):
    with pytest.raises(HarnessContractError, match="host-domain"):
        reject_host_domain_fields({key: "x"})


def test_native_caller_from_mapping_rejects_unknown_keys():
    with pytest.raises(HarnessContractError, match="unexpected"):
        native_caller_from_mapping(
            {
                "agent_id": "a",
                "user_id": "u",
                "session_id": "s",
                "track_id": "t-1",
            }
        )


def test_legal_turn_run_path_accepts_tool_wait_and_complete():
    assert_turn_run_transition(TurnRunState.ACCEPTED, TurnRunState.RUNNING)
    assert_turn_run_transition(TurnRunState.RUNNING, TurnRunState.WAITING_TOOL)
    assert_turn_run_transition(TurnRunState.WAITING_TOOL, TurnRunState.RUNNING)
    assert_turn_run_transition(TurnRunState.RUNNING, TurnRunState.COMPLETED)


@pytest.mark.parametrize(
    "src,dst",
    [
        (TurnRunState.ACCEPTED, TurnRunState.WAITING_TOOL),
        (TurnRunState.COMPLETED, TurnRunState.RUNNING),
        (TurnRunState.FAILED, TurnRunState.RUNNING),
        (TurnRunState.CANCELLED, TurnRunState.RUNNING),
        (TurnRunState.RECOVERY_REQUIRED, TurnRunState.RUNNING),
        (TurnRunState.WAITING_APPROVAL, TurnRunState.WAITING_TOOL),
    ],
)
def test_illegal_turn_run_transitions_are_rejected(src, dst):
    with pytest.raises(HarnessContractError, match="transition"):
        assert_turn_run_transition(src, dst)


def test_snapshot_is_keyed_by_id_and_caller():
    caller = NativeCaller("a", "u", "s")
    snap = ToolSurfaceSnapshot(
        snapshot_id="snap-1",
        caller=caller,
        native_tool_names=("find_tool",),
        native_skill_keys=("signup_interview",),
        host_tool_names=(),
        host_skill_keys=(),
        created_at="2026-09-17T00:00:00+00:00",
        expires_at="2099-01-01T00:00:00+00:00",
        revoked=False,
    )
    assert snap.cache_key() == ("snap-1", "a", "u", "s")
    snap.assert_usable()


def test_expired_snapshot_is_not_usable():
    caller = NativeCaller("a", "u", "s")
    snap = ToolSurfaceSnapshot(
        snapshot_id="snap-old",
        caller=caller,
        native_tool_names=(),
        native_skill_keys=(),
        host_tool_names=(),
        host_skill_keys=(),
        created_at="2020-01-01T00:00:00+00:00",
        expires_at="2020-01-02T00:00:00+00:00",
        revoked=False,
    )
    with pytest.raises(HarnessContractError, match="expired"):
        snap.assert_usable()


def test_revoked_snapshot_is_not_usable_after_flag():
    caller = NativeCaller("a", "u", "s")
    snap = ToolSurfaceSnapshot(
        snapshot_id="snap-1",
        caller=caller,
        native_tool_names=(),
        native_skill_keys=(),
        host_tool_names=(),
        host_skill_keys=(),
        created_at="2026-09-17T00:00:00+00:00",
        expires_at="2099-01-01T00:00:00+00:00",
        revoked=True,
    )
    with pytest.raises(HarnessContractError, match="revoked"):
        snap.assert_usable()


def test_model_payload_cannot_carry_authority():
    with pytest.raises(HarnessContractError, match="authority"):
        reject_model_authority_fields({"q": "hi", "capability_token": "secret"})


@pytest.mark.parametrize(
    "key",
    [
        "work_item_id",
        "lease_token",
        "lease_fence",
        "effect_key",
        "logical_step_key",
        "work_execution_context",
    ],
)
def test_model_payload_cannot_forge_work_execution_authority(key):
    with pytest.raises(HarnessContractError, match="authority"):
        reject_model_authority_fields({"q": "hi", key: "forged"})


def test_host_work_execution_context_reads_visitor_data_only():
    class _Visitor:
        data = {
            "run_id": "workrun:abc",
            "work_item_id": "o.WorkItem.1",
            "lease_token": "tok",
            "lease_fence": 3,
            "noise": "ignore-me",
        }

    ctx = host_work_execution_context(_Visitor())
    assert ctx == {
        "run_id": "workrun:abc",
        "work_item_id": "o.WorkItem.1",
        "lease_token": "tok",
        "lease_fence": 3,
    }
    assert host_work_execution_context(None) == {}
    assert host_work_execution_context(object()) == {}


def test_idempotency_classes_are_the_three_declared_ones():
    assert {c.value for c in IdempotencyClass} == {
        "idempotent",
        "compensatable",
        "non_retryable",
    }
