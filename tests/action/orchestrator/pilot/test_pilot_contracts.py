"""Pilot contracts reject ambiguous or unscoped persisted state."""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from jvagent.action.orchestrator.pilot.contracts import (
    ConversationalReply,
    EvidenceReference,
    PilotCaller,
    PilotRunContext,
    PilotSnapshot,
    ResearchBrief,
    output_user_text,
    validate_snapshot_for_run,
)


def _snapshot() -> PilotSnapshot:
    return PilotSnapshot(
        caller=PilotCaller(agent_id="agent-1", user_id="user-1", session_id="s-1"),
        skill_id="research",
        skill_digest="skill-sha256",
        config_digest="config-sha256",
        question="What does the source say?",
        evidence=(
            EvidenceReference(
                source_id="source-1",
                url="https://example.test/source",
                title="Source",
                excerpt="A bounded excerpt.",
            ),
        ),
    )


def test_snapshot_round_trip_is_json_safe_and_immutable() -> None:
    original = _snapshot()
    restored = PilotSnapshot.model_validate_json(original.model_dump_json())

    assert restored == original
    assert restored.schema_version == 1
    with pytest.raises(ValidationError):
        original.status = "complete"  # type: ignore[misc]


def test_pilot_run_uses_bounded_live_smoke_defaults() -> None:
    context = PilotRunContext(
        caller=PilotCaller(agent_id="agent-1", user_id="user-1", session_id="s-1"),
        task_id="task-1",
        run_id="run-1",
        skill_id="research",
        skill_digest="skill-sha256",
        config_digest="config-sha256",
    )

    assert context.max_model_requests == 8
    assert context.max_tool_calls == 12
    assert context.max_tool_result_chars == 4000
    assert context.max_total_tokens == 20000
    assert context.max_output_tokens == 2000
    assert context.max_runtime_seconds == 120


def test_snapshot_is_bound_to_full_caller_and_compiled_inputs() -> None:
    snapshot = _snapshot()

    assert (
        validate_snapshot_for_run(
            snapshot,
            caller=snapshot.caller,
            skill_id="research",
            skill_digest="skill-sha256",
            config_digest="config-sha256",
        )
        == snapshot
    )

    changed_caller = PilotCaller(agent_id="agent-1", user_id="user-2", session_id="s-1")
    with pytest.raises(ValueError, match="identity or configuration"):
        validate_snapshot_for_run(
            snapshot,
            caller=changed_caller,
            skill_id="research",
            skill_digest="skill-sha256",
            config_digest="config-sha256",
        )


def test_research_brief_rejects_extra_fields_and_unbounded_text() -> None:
    payload = {
        "question": "q",
        "findings": ["finding"],
        "source_ids": ["source-1"],
        "limitations": [],
        "brief": "result",
    }

    with pytest.raises(ValidationError):
        ResearchBrief.model_validate({**payload, "authority": "admin"})
    with pytest.raises(ValidationError):
        EvidenceReference(source_id="x", excerpt="x" * 1601)
    with pytest.raises(ValidationError):
        PilotSnapshot.model_validate(
            {
                **_snapshot().model_dump(),
                "evidence": [
                    EvidenceReference(source_id=f"source-{idx}").model_dump()
                    for idx in range(31)
                ],
            }
        )


def test_user_facing_brief_cites_only_validated_evidence_sources() -> None:
    output = ResearchBrief(
        question="What did the sources say?",
        findings=("Structured results are validated.",),
        source_ids=("https://example.test/docs",),
        brief="Structured results are validated.",
    )

    rendered = output_user_text(
        output,
        (
            EvidenceReference(
                source_id="https://example.test/docs",
                url="https://example.test/docs?a=1&b=2",
            ),
            EvidenceReference(
                source_id="https://unverified.example/",
                url="https://unverified.example/",
            ),
        ),
    )

    assert rendered == (
        "Structured results are validated.\n\n"
        "Sources: [Source 1](<https://example.test/docs?a=1&b=2>)"
    )


def test_user_facing_conversational_reply_needs_no_external_source():
    output = ConversationalReply(answer="I can help with research questions.")

    assert output_user_text(output) == "I can help with research questions."


def test_snapshot_approval_expiry_round_trips_as_utc_datetime() -> None:
    expiry = datetime(2030, 1, 1, tzinfo=timezone.utc)
    snapshot = _snapshot().model_copy(
        update={
            "status": "waiting_approval",
            "approval_id": "approval-1",
            "approval_payload_digest": "payload-sha256",
            "approval_expires_at": expiry,
        }
    )

    restored = PilotSnapshot.model_validate_json(snapshot.model_dump_json())
    assert restored.approval_expires_at == expiry
