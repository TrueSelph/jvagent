"""Pilot contracts reject ambiguous or unscoped persisted state."""

import pytest
from pydantic import ValidationError

from jvagent.action.orchestrator.pilot.contracts import (
    MAX_PILOT_QUESTION_CHARS,
    ConversationalReply,
    EvidenceReference,
    PilotCaller,
    PilotRunContext,
    PilotSnapshot,
    ResearchBrief,
    ResearchFinding,
    output_user_text,
    validate_snapshot_for_run,
)
from tests.action.orchestrator.pilot.effect_state_fixture import EffectPilotSnapshot


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
    assert restored.schema_version == 7
    with pytest.raises(ValidationError):
        original.status = "complete"  # type: ignore[misc]


def test_production_snapshot_excludes_unimplemented_approval_contracts() -> None:
    payload = _snapshot().model_dump()

    with pytest.raises(ValidationError):
        PilotSnapshot.model_validate({**payload, "status": "waiting_approval"})
    with pytest.raises(ValidationError):
        PilotSnapshot.model_validate({**payload, "approval_id": "approval-1"})
    with pytest.raises(ValidationError):
        PilotSnapshot.model_validate({**payload, "invocations": []})


def test_pilot_run_uses_bounded_live_smoke_defaults() -> None:
    context = PilotRunContext(
        caller=PilotCaller(agent_id="agent-1", user_id="user-1", session_id="s-1"),
        task_id="task-1",
        run_id="run-1",
        skill_id="research",
        skill_digest="skill-sha256",
        config_digest="config-sha256",
    )

    assert context.max_model_requests == 32
    assert context.max_tool_calls == 48
    assert context.max_tool_result_chars == 4000
    assert context.max_total_tokens == 100000
    assert context.max_output_tokens == 20000
    assert context.max_runtime_seconds == 300


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
        "findings": [{"claim": "finding", "source_ids": ["source-1"]}],
        "limitations": [],
    }

    with pytest.raises(ValidationError):
        ResearchBrief.model_validate({**payload, "authority": "admin"})
    long_question = "q" * MAX_PILOT_QUESTION_CHARS
    assert (
        ResearchBrief(
            question=long_question,
            findings=(ResearchFinding(claim="finding", source_ids=("source-1",)),),
        ).question
        == long_question
    )
    with pytest.raises(ValidationError):
        ResearchBrief(
            question=long_question + "q",
            findings=(ResearchFinding(claim="finding", source_ids=("source-1",)),),
        )
    with pytest.raises(ValidationError):
        EvidenceReference(source_id="x", excerpt="x" * 1601)
    with pytest.raises(ValidationError):
        ResearchFinding(claim="Claim", source_ids=())
    with pytest.raises(ValidationError):
        ResearchFinding(claim="Claim", source_ids=("same", "same"))
    with pytest.raises(ValidationError):
        ResearchFinding(
            claim="Claim with untrusted URL https://fake.test",
            source_ids=("source-1",),
        )
    for claim in (
        "This conclusion is verified [Source 99].",
        "This conclusion is verified [99].",
        "This conclusion is verified by Source #99.",
    ):
        with pytest.raises(ValidationError, match="source_ids"):
            ResearchFinding(claim=claim, source_ids=("source-1",))
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
        findings=(
            ResearchFinding(
                claim="Structured results are validated.",
                source_ids=("source-docs",),
                supporting_source_id="source-docs",
                supporting_quote="Structured results are validated.",
            ),
        ),
    )

    reference = EvidenceReference(
        source_id="source-docs",
        url="https://example.test/docs?a=1&b=2",
        excerpt="Structured results are validated.",
        provenance="fetched_page",
    )
    rendered = output_user_text(
        output,
        (
            reference,
            EvidenceReference(
                source_id="https://unverified.example/",
                url="https://unverified.example/",
                provenance="fetched_page",
            ),
        ),
    )

    assert rendered == (
        "- Structured results are validated. [Source 1, observed "
        f"{reference.observed_at.strftime('%Y-%m-%d %H:%M UTC')}](<https://example.test/docs?a=1&b=2>)"
    )


def test_user_facing_brief_preserves_bounded_unverified_limitations() -> None:
    output = ResearchBrief(
        question="What did the sources say?",
        findings=(
            ResearchFinding(
                claim="Structured results are validated.",
                source_ids=("source-docs",),
                supporting_source_id="source-docs",
                supporting_quote="Structured results are validated.",
            ),
        ),
        limitations=("A second source could not be fetched.",),
    )
    reference = EvidenceReference(
        source_id="source-docs",
        url="https://example.test/docs",
        excerpt="Structured results are validated.",
        provenance="fetched_page",
    )

    rendered = output_user_text(output, (reference,))

    assert "Model-reported limitations (not independently verified):" in rendered
    assert "- A second source could not be fetched." in rendered


def test_research_limitations_are_bounded_and_cannot_bypass_citations() -> None:
    with pytest.raises(ValidationError):
        ResearchBrief(
            question="q",
            findings=(
                ResearchFinding(
                    claim="A finding",
                    source_ids=("source-1",),
                    supporting_source_id="source-1",
                    supporting_quote="A supporting quote long enough.",
                ),
            ),
            limitations=("x" * 1001,),
        )

    with pytest.raises(ValidationError, match="URLs or citations"):
        ResearchBrief(
            question="q",
            findings=(
                ResearchFinding(
                    claim="A finding",
                    source_ids=("source-1",),
                    supporting_source_id="source-1",
                    supporting_quote="A supporting quote long enough.",
                ),
            ),
            limitations=("See https://example.test for details.",),
        )

    with pytest.raises(ValidationError, match="URLs or citations"):
        ResearchBrief(
            question="q",
            findings=(
                ResearchFinding(
                    claim="A finding",
                    source_ids=("source-1",),
                    supporting_source_id="source-1",
                    supporting_quote="A supporting quote long enough.",
                ),
            ),
            limitations=("See [Source 99] for details.",),
        )


def test_user_facing_conversational_reply_needs_no_external_source():
    output = ConversationalReply(answer="I can help with research questions.")

    assert output_user_text(output) == "I can help with research questions."


def test_test_only_approval_expiry_round_trips_as_utc_datetime() -> None:
    from datetime import datetime, timezone

    expiry = datetime(2030, 1, 1, tzinfo=timezone.utc)
    snapshot = EffectPilotSnapshot.model_validate(_snapshot().model_dump()).model_copy(
        update={
            "status": "waiting_approval",
            "approval_id": "approval-1",
            "approval_payload_digest": "payload-sha256",
            "approval_expires_at": expiry,
        }
    )

    restored = EffectPilotSnapshot.model_validate_json(snapshot.model_dump_json())
    assert restored.approval_expires_at == expiry
