"""The pilot persists through TaskStore and requires real async graph flushing."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from jvagent.action.orchestrator.continuation import park_capability_pilot_tasks
from jvagent.action.orchestrator.pilot.contracts import (
    EvidenceReference,
    PilotCaller,
    PilotInvocation,
    PilotSnapshot,
    ResearchBrief,
)
from jvagent.action.orchestrator.pilot.state import (
    PILOT_TASK_TYPE,
    PilotStateError,
    PilotTaskStore,
)
from jvagent.memory.conversation import Conversation
from jvagent.memory.task_store import TaskStore
from tests.action.orchestrator.pilot.effect_state_fixture import PilotEffectTestStore


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


@pytest.mark.asyncio
async def test_pilot_task_snapshot_survives_recreation_and_is_caller_scoped():
    durable = []
    conversation = DurableConversation(durable=durable)
    tasks = PilotTaskStore(conversation)
    handle = await tasks.create(
        _snapshot(), title="research", description="Research the question"
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
    unsupported["schema_version"] = 3
    await handle.set_snapshot(unsupported)

    with pytest.raises(
        PilotStateError,
        match=r"schema version 3 is unsupported.*Preserve the task and start a new pilot run",
    ):
        tasks.load(
            handle.id,
            caller=_snapshot().caller,
            skill_id="research",
            skill_digest="sha256-skill",
            config_digest="sha256-config",
        )

    assert handle.status == "active"
    assert handle.snapshot["schema_version"] == 3


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
        findings=("A finding",),
        source_ids=("source-1",),
        brief="A finding supported by source-1.",
    )
    result = _snapshot(status="complete", output=output)
    with pytest.raises(PilotStateError, match="final delivery"):
        await tasks.complete(handle, result, delivered=False)
    await tasks.complete(handle, result, delivered=True)
    assert handle.status == "completed"


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
    invocation = PilotInvocation(
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
    invocation = PilotInvocation(
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
    invocation = PilotInvocation(
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
    tasks = PilotEffectTestStore(conversation)
    parent = await tasks.create(
        _snapshot(), title="research", description="Initial research"
    )
    output = ResearchBrief(
        question="q",
        findings=("A finding",),
        source_ids=("source-1",),
        brief="A finding supported by source-1.",
    )
    await tasks.complete(
        parent, _snapshot(status="complete", output=output), delivered=True
    )

    followup = await tasks.create(
        _snapshot(question="Follow-up question"),
        title="research",
        description="Follow-up research",
        parent_task_id=parent.id,
    )
    assert followup.data["pilot_parent_task_id"] == parent.id

    parked_snapshot = _snapshot(status="parked", park_reason="legacy selected")
    await tasks.park(followup, parked_snapshot, "rollback")
    resumed = await tasks.resume(
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
    tasks = PilotTaskStore(conversation)
    snapshot = _snapshot(
        evidence=(
            EvidenceReference(
                source_id="source-1",
                url="https://example.test/source",
                title="Persisted source",
                excerpt="Evidence retained across rollback.",
            ),
        ),
        invocations=(
            PilotInvocation(
                invocation_id="call-1",
                tool_name="web_search__search",
                payload_digest="sha256-payload",
                status="started",
            ),
        ),
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
async def test_schema_one_task_is_preserved_and_refused_after_rollback(test_db):
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
            "schema_version": 1,
            "approval_id": None,
            "approval_payload_digest": None,
            "approval_expires_at": None,
            "approval_invocation_id": None,
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
    with pytest.raises(PilotStateError, match="schema version 1 is unsupported"):
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
        findings=("A finding",),
        source_ids=("source-1",),
        brief="A finding supported by source-1.",
    )
    conversation.fail_flush = True

    with pytest.raises(OSError, match="storage unavailable"):
        await tasks.complete(
            handle, _snapshot(status="complete", output=output), delivered=True
        )

    assert handle.status == "active"
    assert handle.snapshot["status"] == "running"
    assert conversation.durable[0]["status"] == "active"
    assert conversation.durable[0]["snapshot"]["status"] == "running"
    assert conversation.tasks[0]["status"] == "active"
    assert conversation.tasks[0]["snapshot"]["status"] == "running"
