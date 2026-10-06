"""The pilot persists through TaskStore and requires real async graph flushing."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from jvagent.action.orchestrator.continuation import park_capability_pilot_tasks
from jvagent.action.orchestrator.pilot.contracts import (
    ConversationalReply,
    EvidenceReference,
    PilotCaller,
    PilotSnapshot,
    ResearchBrief,
    ResearchFinding,
)
from jvagent.action.orchestrator.pilot.state import (
    PILOT_TASK_TYPE,
    PilotStateError,
    PilotTaskStore,
)
from jvagent.memory.conversation import Conversation
from jvagent.memory.task_store import TaskStore
from tests.action.orchestrator.pilot.effect_state_fixture import (
    EffectPilotInvocation,
    EffectPilotSnapshot,
    PilotEffectTestStore,
)


class DurableConversation:
    def __init__(self, tasks=None, durable=None):
        self.tasks = deepcopy(tasks or [])
        self.durable = durable if durable is not None else []
        self.flush_count = 0

    async def flush(self):
        self.durable[:] = deepcopy(self.tasks)
        self.flush_count += 1


class NoPersistenceConversation:
    tasks = []


def _snapshot(**updates) -> PilotSnapshot:
    payload = {
        "caller": PilotCaller(agent_id="a1", user_id="u1", session_id="s1"),
        "skill_id": "research",
        "skill_digest": "sha256-skill",
        "config_digest": "sha256-config",
        "question": "What does the evidence establish?",
    }
    payload.update(updates)
    return PilotSnapshot(**payload)


async def _acknowledged_result(tasks, handle, result):
    """Exercise graph-backed attempt and acknowledgment before completion."""
    pending = result.model_copy(update={"status": "delivery_pending"})
    pending = await tasks.prepare_delivery(handle, pending)
    message_id = f"o.ResponseMessage.test_{handle.id}"
    attempted = await tasks.record_delivery_attempt(
        handle, pending, message_id=message_id
    )
    acknowledged = await tasks.acknowledge_delivery(
        handle, attempted, message_id=message_id
    )
    return acknowledged.model_copy(update={"status": "complete"})


@pytest.mark.asyncio
async def test_pilot_task_snapshot_survives_recreation_and_is_caller_scoped():
    durable = []
    conversation = DurableConversation(durable=durable)
    tasks = PilotTaskStore(conversation)
    handle = await tasks.create(
        _snapshot(evidence_overflow_count=3),
        title="research",
        description="Research the question",
    )
    assert handle.task_type == PILOT_TASK_TYPE
    assert handle.status == "active"
    with pytest.raises(PilotStateError, match="snapshot is invalid"):
        invalid = _snapshot().model_copy(update={"status": "unrecognized"})
        await tasks.save(handle, invalid)

    reloaded_conversation = DurableConversation(tasks=durable, durable=durable)
    reloaded = PilotTaskStore(reloaded_conversation)
    found, snapshot = reloaded.load(
        handle.id,
        caller=PilotCaller(agent_id="a1", user_id="u1", session_id="s1"),
        skill_id="research",
        skill_digest="sha256-skill",
        config_digest="sha256-config",
    )
    assert found.status == "active"
    assert snapshot.evidence_overflow_count == 3
    assert snapshot.question == "What does the evidence establish?"
    assert conversation.flush_count == 1

    with pytest.raises(PilotStateError, match="identity or configuration"):
        reloaded.load(
            handle.id,
            caller=PilotCaller(agent_id="a1", user_id="u2", session_id="s1"),
            skill_id="research",
            skill_digest="sha256-skill",
            config_digest="sha256-config",
        )


@pytest.mark.asyncio
async def test_parked_retry_requires_exact_caller_and_current_configuration():
    conversation = DurableConversation()
    tasks = PilotTaskStore(conversation)
    snapshot = _snapshot()
    handle = await tasks.create(
        snapshot, title="research", description="Retry the same question"
    )
    parked = snapshot.model_copy(update={"status": "parked", "park_reason": "legacy"})
    await handle.park(snapshot=parked.model_dump(mode="json"), reason="legacy")

    candidate = tasks.parked_retry(
        caller=snapshot.caller,
        skill_id=snapshot.skill_id,
        skill_digest=snapshot.skill_digest,
        config_digest=snapshot.config_digest,
        question=snapshot.question,
    )
    assert candidate is not None
    assert candidate[0].id == handle.id
    assert candidate[1] == parked
    assert (
        tasks.parked_retry(
            caller=snapshot.caller,
            skill_id=snapshot.skill_id,
            skill_digest=snapshot.skill_digest,
            config_digest="changed-config",
            question=snapshot.question,
        )
        is None
    )
    assert (
        tasks.parked_retry(
            caller=PilotCaller(agent_id="a1", user_id="u2", session_id="s1"),
            skill_id=snapshot.skill_id,
            skill_digest=snapshot.skill_digest,
            config_digest=snapshot.config_digest,
            question=snapshot.question,
        )
        is None
    )
    assert (
        tasks.parked_retry(
            caller=snapshot.caller,
            skill_id=snapshot.skill_id,
            skill_digest=snapshot.skill_digest,
            config_digest=snapshot.config_digest,
            question="different request",
        )
        is None
    )
    duplicate = await tasks.create(
        snapshot, title="research duplicate", description="Duplicate parked retry"
    )
    await duplicate.park(snapshot=parked.model_dump(mode="json"), reason="legacy")
    assert (
        tasks.parked_retry(
            caller=snapshot.caller,
            skill_id=snapshot.skill_id,
            skill_digest=snapshot.skill_digest,
            config_digest=snapshot.config_digest,
            question=snapshot.question,
        )
        is None
    )

    resumed = await tasks.resume_parked_retry(
        handle,
        parked,
        caller=snapshot.caller,
        skill_id=snapshot.skill_id,
        skill_digest=snapshot.skill_digest,
        config_digest=snapshot.config_digest,
        question=snapshot.question,
    )
    assert handle.status == "active"
    assert resumed.status == "running"
    assert resumed.park_reason is None


@pytest.mark.asyncio
async def test_parked_retry_distinguishes_requests_after_old_clip_boundary():
    conversation = DurableConversation()
    tasks = PilotTaskStore(conversation)
    first_question = "q" * 2000 + " first ending"
    snapshot = _snapshot().model_copy(update={"question": first_question})
    handle = await tasks.create(
        snapshot, title="research", description="Request with long prefix"
    )
    parked = snapshot.model_copy(update={"status": "parked", "park_reason": "legacy"})
    await handle.park(snapshot=parked.model_dump(mode="json"), reason="legacy")

    different_question = "q" * 2000 + " different ending"
    assert (
        tasks.parked_retry(
            caller=snapshot.caller,
            skill_id=snapshot.skill_id,
            skill_digest=snapshot.skill_digest,
            config_digest=snapshot.config_digest,
            question=different_question,
        )
        is None
    )


@pytest.mark.asyncio
async def test_interrupted_active_run_is_failed_with_checkpoint_preserved():
    conversation = DurableConversation()
    tasks = PilotTaskStore(conversation)
    checkpoint = _snapshot(
        evidence=(EvidenceReference(source_id="source-1", url="https://example.test"),)
    )
    handle = await tasks.create(
        checkpoint, title="research", description="Research the question"
    )

    found = tasks.active_run(
        caller=checkpoint.caller,
        skill_id=checkpoint.skill_id,
        skill_digest=checkpoint.skill_digest,
        config_digest=checkpoint.config_digest,
    )
    assert found is not None
    recovered_handle, recovered_snapshot = found
    assert recovered_handle.id == handle.id
    assert recovered_snapshot == checkpoint
    failed = await tasks.fail_interrupted(recovered_handle, recovered_snapshot)

    assert failed.status == "failed"
    assert failed.evidence == checkpoint.evidence
    assert recovered_handle.status == "failed"
    assert recovered_handle.snapshot["status"] == "failed"
    assert recovered_handle.data["failure_reason"] == (
        "interrupted run requires an explicit restart"
    )
    assert (
        tasks.active_run(
            caller=checkpoint.caller,
            skill_id=checkpoint.skill_id,
            skill_digest=checkpoint.skill_digest,
            config_digest=checkpoint.config_digest,
        )
        is None
    )


@pytest.mark.asyncio
async def test_ambiguous_active_runs_fail_closed_without_creating_another_task():
    conversation = DurableConversation()
    tasks = PilotTaskStore(conversation)
    checkpoint = _snapshot()
    first = await tasks.create(
        checkpoint, title="research one", description="First active run"
    )
    second = await tasks.create(
        checkpoint, title="research two", description="Second active run"
    )

    with pytest.raises(PilotStateError, match="multiple active pilot runs"):
        tasks.active_run(
            caller=checkpoint.caller,
            skill_id=checkpoint.skill_id,
            skill_digest=checkpoint.skill_digest,
            config_digest=checkpoint.config_digest,
        )

    assert first.status == "active"
    assert second.status == "active"
    assert len(conversation.tasks) == 2


@pytest.mark.asyncio
async def test_failed_exact_retry_parent_preserves_cumulative_usage():
    conversation = DurableConversation()
    tasks = PilotTaskStore(conversation)
    first = _snapshot(
        status="failed",
        usage_accounting_complete=False,
        model_requests_used=3,
        unsettled_model_requests=0,
        unreported_model_usage_responses=1,
        tool_calls_used=7,
        reported_input_tokens_used=1200,
        reported_output_tokens_used=800,
        estimated_input_tokens_used=340,
        estimated_output_tokens_used=55,
    )
    handle = await tasks.create(first, title="research", description=first.question)
    await tasks.fail(handle, first, "provider error")

    parent = tasks.failed_retry_parent(
        caller=first.caller,
        skill_id=first.skill_id,
        skill_digest=first.skill_digest,
        config_digest=first.config_digest,
        question=first.question,
    )
    assert parent is not None
    assert parent[0].id == handle.id
    assert parent[1].usage_accounting_complete is False
    assert parent[1].model_requests_used == 3
    assert parent[1].unsettled_model_requests == 0
    assert parent[1].unreported_model_usage_responses == 1
    assert parent[1].tool_calls_used == 7
    assert parent[1].reported_input_tokens_used == 1200
    assert parent[1].reported_output_tokens_used == 800
    assert parent[1].estimated_input_tokens_used == 340
    assert parent[1].estimated_output_tokens_used == 55


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy_version", [4, 5])
async def test_legacy_snapshot_migrates_without_claiming_unknown_usage_is_zero(
    legacy_version,
):
    conversation = DurableConversation()
    tasks = PilotTaskStore(conversation)
    snapshot = _snapshot()
    handle = await tasks.create(
        snapshot, title="research", description=snapshot.question
    )
    legacy = snapshot.model_dump(mode="json")
    legacy["schema_version"] = legacy_version
    if legacy_version == 4:
        accounting_fields = (
            "usage_accounting_complete",
            "model_requests_used",
            "tool_calls_used",
            "reported_input_tokens_used",
            "reported_output_tokens_used",
            "estimated_input_tokens_used",
            "estimated_output_tokens_used",
        )
        for key in accounting_fields:
            legacy.pop(key)
    else:
        legacy["model_requests_used"] = 2
        legacy["tool_calls_used"] = 3
        legacy["reported_input_tokens_used"] = 50
        legacy["reported_output_tokens_used"] = 20
        legacy.pop("unsettled_model_requests")
        legacy.pop("unreported_model_usage_responses")
    await handle.set_snapshot(legacy)

    loaded_handle, restored = tasks.load(
        handle.id,
        caller=snapshot.caller,
        skill_id=snapshot.skill_id,
        skill_digest=snapshot.skill_digest,
        config_digest=snapshot.config_digest,
    )
    assert restored.schema_version == 7
    assert restored.usage_accounting_complete is False
    assert restored.model_requests_used == (2 if legacy_version == 5 else 0)
    assert (
        tasks.failed_retry_parent(
            caller=snapshot.caller,
            skill_id=snapshot.skill_id,
            skill_digest=snapshot.skill_digest,
            config_digest=snapshot.config_digest,
            question=snapshot.question,
        )
        is None
    )
    await tasks.fail_interrupted(loaded_handle, restored)
    assert loaded_handle.snapshot["usage_accounting_complete"] is False
    parent = tasks.failed_retry_parent(
        caller=snapshot.caller,
        skill_id=snapshot.skill_id,
        skill_digest=snapshot.skill_digest,
        config_digest=snapshot.config_digest,
        question=snapshot.question,
    )
    assert parent is not None
    assert parent[1].unsettled_model_requests == 1
    assert parent[1].usage_accounting_complete is False


@pytest.mark.asyncio
async def test_v6_snapshot_migrates_to_unacknowledged_delivery_state():
    conversation = DurableConversation()
    tasks = PilotTaskStore(conversation)
    snapshot = _snapshot()
    handle = await tasks.create(
        snapshot, title="research", description=snapshot.question
    )
    legacy = snapshot.model_dump(mode="json")
    legacy["schema_version"] = 6
    for field in (
        "delivery_attempt_count",
        "delivery_message_id",
        "delivery_last_attempt_at",
        "delivery_acknowledged",
        "delivery_acknowledged_at",
    ):
        legacy.pop(field)
    await handle.set_snapshot(legacy)

    loaded_handle, restored = tasks.load(
        handle.id,
        caller=snapshot.caller,
        skill_id=snapshot.skill_id,
        skill_digest=snapshot.skill_digest,
        config_digest=snapshot.config_digest,
    )

    assert loaded_handle.id == handle.id
    assert restored.schema_version == 7
    assert restored.delivery_attempt_count == 0
    assert restored.delivery_message_id is None
    assert restored.delivery_acknowledged is False


@pytest.mark.asyncio
async def test_delivery_attempt_and_acknowledgment_survive_taskstore_recreation():
    durable = []
    conversation = DurableConversation(durable=durable)
    tasks = PilotTaskStore(conversation)
    running = _snapshot()
    handle = await tasks.create(running, title="research", description=running.question)
    pending = running.model_copy(
        update={
            "status": "delivery_pending",
            "evidence": (
                EvidenceReference(
                    source_id="source-1",
                    url="https://example.test/source",
                    excerpt="A supported source quote.",
                    provenance="fetched_page",
                ),
            ),
            "output": ResearchBrief(
                question=running.question,
                findings=(
                    ResearchFinding(
                        claim="A supported finding.",
                        source_ids=("source-1",),
                        supporting_source_id="source-1",
                        supporting_quote="A supported source quote.",
                    ),
                ),
            ),
        }
    )
    pending = await tasks.prepare_delivery(handle, pending)
    message_id = "o.ResponseMessage.pilot_0123456789abcdef01234567"
    attempted = await tasks.record_delivery_attempt(
        handle, pending, message_id=message_id
    )
    assert attempted.delivery_attempt_count == 1
    assert attempted.delivery_message_id == message_id

    recovered_store = PilotTaskStore(
        DurableConversation(tasks=durable, durable=durable)
    )
    active = recovered_store.active_run(
        caller=running.caller,
        skill_id=running.skill_id,
        skill_digest=running.skill_digest,
        config_digest=running.config_digest,
    )
    assert active is not None
    recovered_handle, recovered_snapshot = active
    assert recovered_snapshot.delivery_attempt_count == 1
    assert recovered_snapshot.delivery_acknowledged is False

    acknowledged = await recovered_store.acknowledge_delivery(
        recovered_handle, recovered_snapshot, message_id=message_id
    )
    second_recovery = PilotTaskStore(
        DurableConversation(tasks=durable, durable=durable)
    )
    active_after_ack = second_recovery.active_run(
        caller=running.caller,
        skill_id=running.skill_id,
        skill_digest=running.skill_digest,
        config_digest=running.config_digest,
    )
    assert active_after_ack is not None
    _, acknowledged_snapshot = active_after_ack
    assert acknowledged_snapshot.delivery_acknowledged is True
    assert acknowledged_snapshot.delivery_acknowledged_at == (
        acknowledged.delivery_acknowledged_at
    )


@pytest.mark.asyncio
async def test_rehydrate_rejects_task_snapshot_status_mismatch():
    conversation = DurableConversation()
    tasks = PilotTaskStore(conversation)
    handle = await tasks.create(
        _snapshot(), title="research", description="Research the question"
    )
    corrupted = _snapshot(status="complete")
    await handle.set_snapshot(corrupted.model_dump(mode="json"))

    with pytest.raises(PilotStateError, match="lifecycle statuses do not match"):
        tasks.load(
            handle.id,
            caller=corrupted.caller,
            skill_id=corrupted.skill_id,
            skill_digest=corrupted.skill_digest,
            config_digest=corrupted.config_digest,
        )


@pytest.mark.asyncio
async def test_rehydrate_reports_unsupported_snapshot_version_with_recovery():
    conversation = DurableConversation()
    tasks = PilotTaskStore(conversation)
    handle = await tasks.create(
        _snapshot(), title="research", description="Research the question"
    )
    unsupported = deepcopy(handle.snapshot)
    unsupported["schema_version"] = 8
    await handle.set_snapshot(unsupported)

    with pytest.raises(
        PilotStateError,
        match=r"schema version 8 is unsupported.*Preserve the task and start a new pilot run",
    ):
        tasks.load(
            handle.id,
            caller=_snapshot().caller,
            skill_id="research",
            skill_digest="sha256-skill",
            config_digest="sha256-config",
        )

    assert handle.status == "active"
    assert handle.snapshot["schema_version"] == 8


@pytest.mark.asyncio
async def test_v3_snapshot_is_preserved_but_not_resumed_after_claim_schema_change():
    conversation = DurableConversation()
    tasks = PilotTaskStore(conversation)
    handle = await tasks.create(
        _snapshot(), title="research", description="Research the question"
    )
    legacy = deepcopy(handle.snapshot)
    legacy["schema_version"] = 3
    await handle.set_snapshot(legacy)

    with pytest.raises(PilotStateError, match="schema version 3 is unsupported"):
        tasks.load(
            handle.id,
            caller=_snapshot().caller,
            skill_id="research",
            skill_digest="sha256-skill",
            config_digest="sha256-config",
        )
    assert handle.snapshot["schema_version"] == 3
    assert handle.status == "active"


@pytest.mark.asyncio
async def test_pilot_snapshot_round_trips_through_the_real_json_graph(test_db):
    conversation = await Conversation.create(
        session_id="pilot-graph-round-trip",
        user_id="pilot-user",
        channel="default",
    )
    caller = PilotCaller(
        agent_id="pilot-agent",
        user_id="pilot-user",
        session_id="pilot-graph-round-trip",
    )
    state = PilotTaskStore(conversation)
    snapshot = _snapshot(caller=caller)
    try:
        handle = await state.create(
            snapshot,
            task_id="pilot_graph_round_trip",
            title="Pilot graph round trip",
            description="Persist and reload a typed pilot snapshot",
        )
        persisted = snapshot.model_copy(
            update={
                "evidence": (
                    EvidenceReference(
                        source_id="https://example.test/source",
                        url="https://example.test/source",
                        title="Graph-backed source",
                        excerpt="Persisted evidence.",
                    ),
                )
            }
        )
        await state.save(handle, persisted)

        reloaded_conversation = await Conversation.get(conversation.id)
        assert reloaded_conversation is not None
        loaded_handle, loaded_snapshot = PilotTaskStore(reloaded_conversation).load(
            handle.id,
            caller=caller,
            skill_id="research",
            skill_digest="sha256-skill",
            config_digest="sha256-config",
        )
        assert loaded_handle.status == "active"
        assert loaded_snapshot.evidence[0].source_id == "https://example.test/source"
    finally:
        await conversation.delete(cascade=True)


@pytest.mark.asyncio
async def test_task_completion_requires_validated_output_and_delivery():
    conversation = DurableConversation()
    tasks = PilotTaskStore(conversation)
    handle = await tasks.create(
        _snapshot(), title="research", description="Research the question"
    )
    output = ResearchBrief(
        question="q",
        findings=(
            ResearchFinding(
                claim="A finding",
                source_ids=("source-1",),
                supporting_source_id="source-1",
                supporting_quote="A finding",
            ),
        ),
    )
    result = _snapshot(
        status="complete",
        evidence=(
            EvidenceReference(
                source_id="source-1",
                url="https://example.test/1",
                excerpt="A finding appears in the source.",
                provenance="fetched_page",
            ),
        ),
        output=output,
    )
    with pytest.raises(PilotStateError, match="final delivery"):
        await tasks.complete(handle, result, delivered=False)
    acknowledged_result = await _acknowledged_result(tasks, handle, result)
    changed_after_ack = acknowledged_result.model_copy(
        update={
            "output": output.model_copy(
                update={
                    "findings": (
                        output.findings[0].model_copy(
                            update={"claim": "A changed answer after acknowledgment."}
                        ),
                    )
                }
            )
        }
    )
    with pytest.raises(PilotStateError, match="persisted delivery acknowledgement"):
        await tasks.complete(handle, changed_after_ack, delivered=True)
    await tasks.complete(handle, acknowledged_result, delivered=True)
    assert handle.status == "completed"


@pytest.mark.asyncio
async def test_task_completion_rejects_source_free_conversational_variant():
    conversation = DurableConversation()
    tasks = PilotTaskStore(conversation)
    handle = await tasks.create(
        _snapshot(), title="research", description="Research the question"
    )
    result = _snapshot(
        status="complete",
        output=ConversationalReply(answer="A factual answer without evidence."),
    )

    with pytest.raises(PilotStateError, match="evidence-backed ResearchBrief"):
        await tasks.complete(handle, result, delivered=True)

    assert handle.status == "active"


@pytest.mark.asyncio
async def test_task_completion_revalidates_quote_and_fresh_source_contract():
    conversation = DurableConversation()
    tasks = PilotTaskStore(conversation)
    handle = await tasks.create(
        _snapshot(), title="research", description="Research the question"
    )
    result = _snapshot(
        status="complete",
        evidence=(
            EvidenceReference(
                source_id="source-1",
                url="https://example.test/1",
                excerpt="A different statement is on this fetched page.",
                provenance="fetched_page",
            ),
        ),
        output=ResearchBrief(
            question="q",
            findings=(
                ResearchFinding(
                    claim="A finding",
                    source_ids=("source-1",),
                    supporting_source_id="source-1",
                    supporting_quote="This fabricated quote is not in the page.",
                ),
            ),
        ),
    )

    with pytest.raises(PilotStateError, match="evidence contract"):
        await tasks.complete(handle, result, delivered=True)

    assert handle.status == "active"


@pytest.mark.asyncio
async def test_delivery_pending_output_is_durable_and_recovered_as_active():
    durable = []
    conversation = DurableConversation(durable=durable)
    tasks = PilotTaskStore(conversation)
    handle = await tasks.create(
        _snapshot(), title="research", description="Research the question"
    )
    pending = _snapshot(
        status="delivery_pending",
        evidence=(
            EvidenceReference(
                source_id="source-1",
                url="https://example.test/1",
                excerpt="A finding appears in the source.",
                provenance="fetched_page",
            ),
        ),
        output=ResearchBrief(
            question="q",
            findings=(
                ResearchFinding(
                    claim="A finding",
                    source_ids=("source-1",),
                    supporting_source_id="source-1",
                    supporting_quote="A finding appears in the source.",
                ),
            ),
        ),
    )

    await tasks.prepare_delivery(handle, pending)

    recovered = PilotTaskStore(DurableConversation(tasks=durable, durable=durable))
    active = recovered.active_run(
        caller=pending.caller,
        skill_id=pending.skill_id,
        skill_digest=pending.skill_digest,
        config_digest=pending.config_digest,
    )
    assert active is not None
    recovered_handle, recovered_snapshot = active
    assert recovered_handle.status == "active"
    assert recovered_snapshot.status == "delivery_pending"
    assert recovered_snapshot.output == pending.output


@pytest.mark.asyncio
async def test_cancellation_persists_typed_status_before_task_terminal_state():
    conversation = DurableConversation()
    tasks = PilotTaskStore(conversation)
    handle = await tasks.create(
        _snapshot(), title="research", description="Research the question"
    )
    cancelled = _snapshot(status="cancelled")

    await tasks.cancel(handle, cancelled, "operator cancelled")

    assert handle.status == "cancelled"
    assert handle.snapshot["status"] == "cancelled"


@pytest.mark.asyncio
async def test_approval_parking_preserves_state_and_requires_new_decision():
    conversation = DurableConversation()
    tasks = PilotEffectTestStore(conversation)
    handle = await tasks.create(
        _snapshot(), title="research", description="Research the question"
    )
    invocation = EffectPilotInvocation(
        invocation_id="invocation-1",
        tool_name="fake_service__write",
        payload_digest="payload-sha256",
    )
    running = await tasks.record_invocation(handle, _snapshot(), invocation)
    expiry = datetime.now(timezone.utc) + timedelta(minutes=5)
    waiting = await tasks.wait_for_approval(
        handle,
        running,
        invocation_id=invocation.invocation_id,
        approval_id="approval-1",
        payload_digest=invocation.payload_digest,
        expires_at=expiry,
    )
    assert handle.status == "parked"
    _, persisted = tasks.load(
        handle.id,
        caller=waiting.caller,
        skill_id="research",
        skill_digest="sha256-skill",
        config_digest="sha256-config",
    )
    assert persisted.status == "parked"
    assert persisted.approval_id == "approval-1"
    with pytest.raises(PilotStateError, match="approval is missing"):
        await tasks.resume(
            handle,
            caller=waiting.caller,
            skill_id="research",
            skill_digest="sha256-skill",
            config_digest="sha256-config",
        )
    with pytest.raises(PilotStateError, match="approval is missing"):
        await tasks.resume(
            handle,
            caller=waiting.caller,
            skill_id="research",
            skill_digest="sha256-skill",
            config_digest="sha256-config",
            approval_id="approval-1",
            approval_payload_digest="changed-payload",
        )
    with pytest.raises(PilotStateError, match="approval is missing"):
        await tasks.resume(
            handle,
            caller=waiting.caller,
            skill_id="research",
            skill_digest="sha256-skill",
            config_digest="sha256-config",
            approval_id="approval-1",
            approval_payload_digest=invocation.payload_digest,
            now=expiry + timedelta(seconds=1),
        )
    with pytest.raises(PilotStateError, match="identity or configuration"):
        await tasks.resume(
            handle,
            caller=PilotCaller(agent_id="a1", user_id="u2", session_id="s1"),
            skill_id="research",
            skill_digest="sha256-skill",
            config_digest="sha256-config",
            approval_id="approval-1",
            approval_payload_digest=invocation.payload_digest,
        )
    with pytest.raises(PilotStateError, match="identity or configuration"):
        await tasks.resume(
            handle,
            caller=waiting.caller,
            skill_id="changed_skill",
            skill_digest="sha256-skill",
            config_digest="sha256-config",
            approval_id="approval-1",
            approval_payload_digest=invocation.payload_digest,
        )
    resumed = await tasks.resume(
        handle,
        caller=waiting.caller,
        skill_id="research",
        skill_digest="sha256-skill",
        config_digest="sha256-config",
        approval_id="approval-1",
        approval_payload_digest=invocation.payload_digest,
    )
    assert handle.status == "active"
    assert resumed.approval_id is None
    assert resumed.invocations[0] == invocation


@pytest.mark.asyncio
async def test_started_invocation_is_parked_for_reconciliation_and_cannot_resume():
    conversation = DurableConversation()
    tasks = PilotEffectTestStore(conversation)
    handle = await tasks.create(
        _snapshot(), title="effect", description="Run a fake effect"
    )
    invocation = EffectPilotInvocation(
        invocation_id="invocation-uncertain",
        tool_name="fake_service__write",
        payload_digest="payload-sha256",
    )
    state = await tasks.record_invocation(handle, _snapshot(), invocation)
    started = await tasks.mark_invocation_started(
        handle, state, invocation.invocation_id
    )
    parked = await tasks.require_reconciliation(
        handle, started, "worker ended after effect start"
    )
    assert handle.status == "parked"
    assert parked.requires_reconciliation
    assert parked.invocations[0].status == "started"
    with pytest.raises(PilotStateError, match="reconciliation"):
        await tasks.resume(
            handle,
            caller=parked.caller,
            skill_id="research",
            skill_digest="sha256-skill",
            config_digest="sha256-config",
        )


@pytest.mark.asyncio
async def test_settled_invocation_receipt_is_persisted_before_reuse():
    durable = []
    conversation = DurableConversation(durable=durable)
    tasks = PilotEffectTestStore(conversation)
    handle = await tasks.create(
        _snapshot(), title="effect", description="Run a fake effect"
    )
    invocation = EffectPilotInvocation(
        invocation_id="invocation-settled",
        tool_name="fake_service__write",
        payload_digest="payload-sha256",
    )
    state = await tasks.record_invocation(handle, _snapshot(), invocation)
    started = await tasks.mark_invocation_started(
        handle, state, invocation.invocation_id
    )
    settled = await tasks.settle_invocation(
        handle, started, invocation.invocation_id, "fake receipt"
    )
    reloaded = PilotEffectTestStore(DurableConversation(tasks=durable, durable=durable))
    _, restored = reloaded.load(
        handle.id,
        caller=settled.caller,
        skill_id=settled.skill_id,
        skill_digest=settled.skill_digest,
        config_digest=settled.config_digest,
    )
    assert restored.invocations[0].status == "settled"
    assert restored.invocations[0].result == "fake receipt"


@pytest.mark.asyncio
async def test_followup_links_prior_task_and_rollback_parks_for_explicit_resume():
    durable = []
    conversation = DurableConversation(durable=durable)
    tasks = PilotTaskStore(conversation)
    parent = await tasks.create(
        _snapshot(), title="research", description="Initial research"
    )
    output = ResearchBrief(
        question="q",
        findings=(
            ResearchFinding(
                claim="A finding",
                source_ids=("source-1",),
                supporting_source_id="source-1",
                supporting_quote="A finding",
            ),
        ),
    )
    parent_result = await _acknowledged_result(
        tasks,
        parent,
        _snapshot(
            status="complete",
            evidence=(
                EvidenceReference(
                    source_id="source-1",
                    url="https://example.test/1",
                    excerpt="A finding appears in the source.",
                    provenance="fetched_page",
                ),
            ),
            output=output,
        ),
    )
    await tasks.complete(parent, parent_result, delivered=True)

    effect_tasks = PilotEffectTestStore(conversation)
    followup = await effect_tasks.create(
        _snapshot(question="Follow-up question"),
        title="research",
        description="Follow-up research",
        parent_task_id=parent.id,
    )
    assert followup.data["pilot_parent_task_id"] == parent.id

    parked_snapshot = _snapshot(status="parked", park_reason="legacy selected")
    await effect_tasks.park(followup, parked_snapshot, "rollback")
    resumed = await effect_tasks.resume(
        followup,
        caller=parked_snapshot.caller,
        skill_id="research",
        skill_digest="sha256-skill",
        config_digest="sha256-config",
    )
    assert followup.status == "active"
    assert resumed.status == "running"


@pytest.mark.asyncio
async def test_legacy_rollback_preserves_graph_snapshot_and_blocks_uncertain_resume(
    test_db,
):
    conversation = await Conversation.create(
        session_id="pilot-rollback-graph",
        user_id="pilot-rollback-user",
        channel="default",
    )
    tasks = PilotEffectTestStore(conversation)
    base_snapshot = _snapshot(
        evidence=(
            EvidenceReference(
                source_id="source-1",
                url="https://example.test/source",
                title="Persisted source",
                excerpt="Evidence retained across rollback.",
            ),
        ),
    )
    snapshot = EffectPilotSnapshot.model_validate(
        {
            **base_snapshot.model_dump(mode="json"),
            "invocations": (
                EffectPilotInvocation(
                    invocation_id="call-1",
                    tool_name="web_search__search",
                    payload_digest="sha256-payload",
                    status="started",
                ).model_dump(mode="json"),
            ),
        }
    )
    handle = await tasks.create(
        snapshot, title="research", description="Rollback safety"
    )

    assert (
        await park_capability_pilot_tasks(SimpleNamespace(conversation=conversation))
        == 1
    )

    reloaded_conversation = await Conversation.get(conversation.id)
    assert reloaded_conversation is not None
    reloaded_store = PilotEffectTestStore(reloaded_conversation)
    parked, persisted_snapshot = reloaded_store.load(
        handle.id,
        caller=snapshot.caller,
        skill_id=snapshot.skill_id,
        skill_digest=snapshot.skill_digest,
        config_digest=snapshot.config_digest,
    )
    assert parked.status == "parked"
    assert persisted_snapshot.status == "parked"
    assert persisted_snapshot.evidence == snapshot.evidence
    assert persisted_snapshot.invocations == snapshot.invocations
    assert persisted_snapshot.requires_reconciliation is True

    with pytest.raises(PilotStateError, match="effect reconciliation"):
        await reloaded_store.resume(
            parked,
            caller=snapshot.caller,
            skill_id=snapshot.skill_id,
            skill_digest=snapshot.skill_digest,
            config_digest=snapshot.config_digest,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy_version", [1, 2])
async def test_old_schema_task_is_preserved_and_refused_after_rollback(
    test_db, legacy_version
):
    conversation = await Conversation.create(
        session_id="pilot-schema-one-rollback",
        user_id="pilot-schema-one-user",
        channel="default",
    )
    legacy_snapshot = _snapshot(
        caller=PilotCaller(
            agent_id="pilot-schema-one-agent",
            user_id="pilot-schema-one-user",
            session_id="pilot-schema-one-rollback",
        )
    ).model_dump(mode="json")
    legacy_snapshot.update(
        {
            "schema_version": legacy_version,
            "approval_id": None,
            "approval_payload_digest": None,
            "approval_expires_at": None,
            "approval_invocation_id": None,
            "requires_reconciliation": False,
            "invocations": [],
        }
    )
    task = await TaskStore(conversation).create(
        title="legacy pilot run",
        description="preserve old pilot task on rollback",
        owner_action="research",
        task_type=PILOT_TASK_TYPE,
        initial_status="active",
        snapshot=legacy_snapshot,
    )
    caller = PilotCaller(**legacy_snapshot["caller"])

    assert (
        await park_capability_pilot_tasks(SimpleNamespace(conversation=conversation))
        == 1
    )

    preserved = TaskStore(conversation).get(task.id)
    assert preserved is not None
    assert preserved.status == "parked"
    assert preserved.snapshot == legacy_snapshot
    assert preserved.data["park_reason"].startswith("legacy driver selected")
    with pytest.raises(
        PilotStateError, match=f"schema version {legacy_version} is unsupported"
    ):
        PilotTaskStore(conversation).load(
            task.id,
            caller=caller,
            skill_id="research",
            skill_digest="sha256-skill",
            config_digest="sha256-config",
        )
    assert preserved.status == "parked"
    await conversation.delete(cascade=True)


@pytest.mark.asyncio
async def test_missing_flush_and_flush_failure_are_not_treated_as_persistence():
    with pytest.raises(PilotStateError, match="asynchronous flush"):
        await PilotTaskStore(NoPersistenceConversation()).create(
            _snapshot(), title="research", description="Research"
        )

    class FailingConversation(DurableConversation):
        async def flush(self):
            raise OSError("storage unavailable")

    with pytest.raises(OSError, match="storage unavailable"):
        await PilotTaskStore(FailingConversation()).create(
            _snapshot(), title="research", description="Research"
        )


@pytest.mark.asyncio
async def test_storage_failure_cannot_complete_a_pilot_task():
    class FaultInjectingConversation(DurableConversation):
        fail_flush = False

        async def flush(self):
            if self.fail_flush:
                raise OSError("storage unavailable")
            await super().flush()

    conversation = FaultInjectingConversation()
    tasks = PilotTaskStore(conversation)
    handle = await tasks.create(_snapshot(), title="research", description="Research")
    output = ResearchBrief(
        question="q",
        findings=(
            ResearchFinding(
                claim="A finding",
                source_ids=("source-1",),
                supporting_source_id="source-1",
                supporting_quote="A finding",
            ),
        ),
    )
    acknowledged_result = await _acknowledged_result(
        tasks,
        handle,
        _snapshot(
            status="complete",
            evidence=(
                EvidenceReference(
                    source_id="source-1",
                    url="https://example.test/1",
                    excerpt="A finding appears in the source.",
                    provenance="fetched_page",
                ),
            ),
            output=output,
        ),
    )
    conversation.fail_flush = True

    with pytest.raises(OSError, match="storage unavailable"):
        await tasks.complete(
            handle,
            acknowledged_result,
            delivered=True,
        )

    assert handle.status == "active"
    assert handle.snapshot["status"] == "delivery_pending"
    assert handle.snapshot["delivery_acknowledged"] is True
    assert conversation.durable[0]["status"] == "active"
    assert conversation.durable[0]["snapshot"]["status"] == "delivery_pending"
    assert conversation.durable[0]["snapshot"]["delivery_acknowledged"] is True
    assert conversation.tasks[0]["status"] == "active"
    assert conversation.tasks[0]["snapshot"]["status"] == "delivery_pending"
