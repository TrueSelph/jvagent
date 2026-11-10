"""Tests for the leadgen field catalogue, capture state, and field validation."""

from __future__ import annotations

import pytest

from jvagent.action.leadgen.engine import (
    MAX_OPTIONAL_FIELDS,
    SYNC_FAILED_DIRECTIVE,
    capture_state,
    handoff_directive,
    next_ask_directive,
    optional_field_keys,
    validate_fields,
)
from jvagent.action.leadgen.spec import (
    FieldDef,
    LeadGenSpec,
    SyncDef,
    fields_reference,
    parse_leadgen_spec,
)


class _Record:
    def __init__(self, data, missing=None):
        self._data = dict(data)
        self._missing = list(missing or [])

    def get_yaml(self):
        return dict(self._data)

    def get_missing_fields(self):
        return list(self._missing)


def _project_spec():
    return LeadGenSpec(
        name="t",
        fields=[
            FieldDef(key="full_name", required=True),
            FieldDef(key="project_description"),
            FieldDef(key="project_location"),
            FieldDef(key="contact_role"),
            FieldDef(
                key="project_budget",
                relevant_when={"any_present": ["project_description"]},
            ),
            FieldDef(
                key="project_timeline",
                relevant_when={"any_present": ["project_description"]},
            ),
        ],
        sync=SyncDef(mode="manual"),
    )


def test_catalogue_lists_every_declared_field_in_order():
    spec = _project_spec()
    ref = fields_reference(spec)
    assert [e["key"] for e in ref] == [f.key for f in spec.fields]
    by_key = {e["key"]: e for e in ref}
    # A filled field stays in the catalogue so the model can correct it.
    assert by_key["project_location"]["required"] is False
    assert by_key["full_name"]["required"] is True


def test_catalogue_carries_relevant_when_as_data():
    by_key = {e["key"]: e for e in fields_reference(_project_spec())}
    assert by_key["project_budget"]["relevant_when"] == {
        "any_present": ["project_description"]
    }
    assert "relevant_when" not in by_key["full_name"]


def test_capture_state_points_at_the_next_ask():
    spec = _project_spec()
    state = capture_state(_Record({"full_name": "Ann"}, missing=["email"]), spec)
    assert set(state) == {
        "fields",
        "missing_fields",
        "next_required",
        "optional_fields",
        "field_reference",
        "directive",
    }
    assert state["missing_fields"] == ["email"]
    assert state["next_required"] == "email"
    assert "`email`" in state["directive"]
    assert state["fields"] == {"full_name": "Ann"}
    # The optional pool is keys only; guidance stays in field_reference once.
    assert state["optional_fields"] == [
        "project_description",
        "project_location",
        "contact_role",
    ]
    for removed in ("next_ask", "next_optional", "next_project_optional", "ask_next"):
        assert removed not in state


def test_capture_state_switches_to_the_optional_pool():
    state = capture_state(_Record({"full_name": "Ann"}, missing=[]), _project_spec())
    assert state["next_required"] is None
    assert "Required fields are complete" in state["directive"]
    assert "`project_description`" in state["directive"]
    assert "only when the visitor answered with a value" in state["directive"]
    assert "Never rotate to another optional after a refusal" in state["directive"]


def test_optional_pool_honours_relevant_when():
    fields = {
        "full_name": "Ann",
        "project_description": "slope repair after heavy rain",
        "project_location": "Region 5",
    }
    keys = optional_field_keys(fields, _project_spec())
    assert "project_budget" in keys
    assert "project_timeline" in keys
    assert "project_location" not in keys


def test_directive_closes_when_nothing_is_open():
    directive = next_ask_directive(None, [])
    assert "Do not ask" in directive
    assert "do not thank the visitor every turn" in directive


def test_directive_acknowledges_then_asks_the_required_field():
    directive = next_ask_directive("full_name", [])
    assert "Acknowledge the captured value naturally" in directive
    assert "`full_name`" in directive
    assert "do not ask any other field" in directive
    assert "would you like to continue or cancel" in directive
    assert "do not thank the visitor every turn" in directive
    assert "Never store a refusal" in directive


def test_directive_acknowledges_then_asks_the_optional_field():
    directive = next_ask_directive(None, ["project_budget"])
    assert "Acknowledge the captured value naturally" in directive
    assert "`project_budget`" in directive
    assert "only when the visitor answered with a value" in directive
    assert "Never rotate to another optional after a refusal" in directive
    assert "optional fields never block the request" in directive


def test_handoff_directive_asks_the_one_the_request_needs():
    closed = handoff_directive([])
    assert "ask no further field" in closed
    assert "Acknowledge" in closed
    open_pool = handoff_directive(["contact_role", "company_industry"])
    assert "hand-off sentence" in open_pool
    assert "`contact_role`" in open_pool
    assert "engaged with a value" in open_pool
    assert "A refusal or ignore moves on" in open_pool


def test_handoff_directive_does_not_repeat_after_the_first():
    repeat = handoff_directive(["project_location"], already_stated=True)
    assert "do not repeat the hand-off sentence" in repeat
    assert "Acknowledge" in repeat
    assert "engaged with a value" in repeat
    assert "hand-off sentence" in repeat
    assert "Reply with the hand-off sentence" not in repeat


def test_handoff_directive_acknowledges_when_pool_closed_and_stated():
    repeat = handoff_directive([], already_stated=True)
    assert "do not repeat the hand-off sentence" in repeat
    assert "ask no field" in repeat


def test_optional_pool_is_capped_at_max_optional_fields():
    keys = [f"opt_{i}" for i in range(MAX_OPTIONAL_FIELDS + 2)]
    spec = LeadGenSpec(
        name="t",
        fields=[FieldDef(key="full_name", required=True)]
        + [FieldDef(key=key) for key in keys],
        sync=SyncDef(),
    )
    pool = optional_field_keys({"full_name": "Ann"}, spec)
    assert pool == keys[:MAX_OPTIONAL_FIELDS]


def test_optional_pool_drops_a_declined_gate_key():
    spec = LeadGenSpec(
        name="t",
        fields=[
            FieldDef(key="organization", required=True, decline_value="Personal"),
            FieldDef(
                key="contact_role",
                relevant_when={
                    "any_present": ["organization"],
                    "not_declined": ["organization"],
                },
            ),
            FieldDef(
                key="company_industry",
                relevant_when={"any_present": ["contact_role"]},
            ),
        ],
        sync=SyncDef(mode="manual"),
    )
    retail = {"organization": "personal"}
    assert optional_field_keys(retail, spec) == []
    business = {"organization": "GT Construction Ltd"}
    assert optional_field_keys(business, spec) == ["contact_role"]


def test_optional_pool_never_offers_a_capture_only_field():
    spec = LeadGenSpec(
        name="t",
        fields=[
            FieldDef(key="full_name", required=True),
            FieldDef(key="quote_items", capture_only=True),
            FieldDef(key="additional_details", capture_only=True),
            FieldDef(key="contact_role"),
        ],
        sync=SyncDef(mode="manual"),
    )
    assert optional_field_keys({"full_name": "Ann"}, spec) == ["contact_role"]


def test_parse_relevant_when():
    spec = parse_leadgen_spec(
        {
            "fields": [
                {"key": "project_description"},
                {
                    "key": "project_budget",
                    "relevant_when": {"any_present": ["project_description"]},
                },
            ],
        },
        source_dir="/tmp",
        default_name="s",
    )
    f = spec.get_field("project_budget")
    assert f is not None
    assert f.relevant_when == {"any_present": ["project_description"]}
    ref = next(e for e in fields_reference(spec) if e["key"] == "project_budget")
    assert ref["relevant_when"]["any_present"] == ["project_description"]


def test_parse_rejects_gap_fill_priority():
    """The soft order moved into catalogue position; a stale key must fail fast."""
    with pytest.raises(ValueError, match="priority"):
        parse_leadgen_spec(
            {
                "fields": [{"key": "project_description"}],
                "gap_fill": {"priority": ["project_description"]},
            },
            source_dir="/tmp",
            default_name="s",
        )


def test_parse_rejects_qualification_queues():
    with pytest.raises(ValueError, match="qualification"):
        parse_leadgen_spec(
            {
                "fields": [{"key": "a"}],
                "gap_fill": {"qualification": ["a"]},
            },
            source_dir="/tmp",
            default_name="s",
        )


@pytest.mark.asyncio
async def test_validate_fields_keeps_valid_and_reports_errors():
    spec = LeadGenSpec(
        name="t",
        fields=[
            FieldDef(key="full_name", validator="person_name"),
            FieldDef(key="email", validator="email"),
        ],
    )
    validated, errors = await validate_fields(
        {"full_name": "Ann", "email": "bad"}, spec
    )
    assert validated["full_name"] == "Ann"
    assert "email" not in validated
    assert "email" in errors


@pytest.mark.asyncio
async def test_validate_fields_accepts_decline_value():
    spec = LeadGenSpec(
        name="t",
        fields=[FieldDef(key="email", validator="email", decline_value="N/A")],
    )
    validated, errors = await validate_fields({"email": "N/A"}, spec)
    assert validated["email"] == "N/A"
    assert errors == {}


@pytest.mark.asyncio
async def test_phone_locale_applies_to_any_phone_field():
    spec = LeadGenSpec(name="t", fields=[FieldDef(key="mobile", phone_locale="GY")])
    validated, errors = await validate_fields({"mobile": "6001234"}, spec)
    assert errors == {}
    assert validated["mobile"] == "+592 600 1234"


@pytest.mark.asyncio
async def test_unknown_phone_locale_falls_back_to_e164():
    spec = LeadGenSpec(name="t", fields=[FieldDef(key="mobile", phone_locale="ZZ")])
    validated, errors = await validate_fields({"mobile": "6001234"}, spec)
    assert errors == {}
    assert validated["mobile"] == "+6001234"


@pytest.mark.asyncio
async def test_literal_decline_sentinel_maps_to_the_field_value():
    spec = LeadGenSpec(
        name="t",
        fields=[FieldDef(key="company_industry", decline_value="Not provided")],
    )
    validated, errors = await validate_fields(
        {"company_industry": "decline_value"}, spec
    )
    assert errors == {}
    assert validated["company_industry"] == "Not provided"


@pytest.mark.asyncio
async def test_refusal_phrase_maps_to_the_field_value():
    spec = LeadGenSpec(
        name="t",
        fields=[FieldDef(key="company_industry", decline_value="Not provided")],
    )
    for phrase in (
        "no",
        "none",
        "I don't want to say",
        "n/a",
        "skip",
        "no organization",
    ):
        validated, errors = await validate_fields({"company_industry": phrase}, spec)
        assert errors == {}, phrase
        assert validated["company_industry"] == "Not provided", phrase


@pytest.mark.asyncio
async def test_sentinel_on_a_field_without_a_decline_value_is_rejected():
    spec = LeadGenSpec(name="t", fields=[FieldDef(key="full_name", required=True)])
    validated, errors = await validate_fields({"full_name": "decline_value"}, spec)
    assert "full_name" not in validated
    assert "full_name" in errors


def test_any_present_ignores_a_declined_prerequisite():
    spec = LeadGenSpec(
        name="t",
        fields=[
            FieldDef(key="project_description", decline_value="Not provided"),
            FieldDef(
                key="project_pain_points",
                relevant_when={"any_present": ["project_description"]},
            ),
        ],
    )
    fields = {"project_description": "Not provided"}
    assert "project_pain_points" not in optional_field_keys(fields, spec)
    fields = {"project_description": "Slope repair after heavy rain"}
    assert "project_pain_points" in optional_field_keys(fields, spec)


def test_optional_directive_closes_after_a_decline():
    text = next_ask_directive(None, ["company_size"], just_declined=True)
    assert "ask no further" in text.lower()
    assert "company_size" not in text


def test_optional_directive_is_conditional_without_a_decline():
    text = next_ask_directive(None, ["company_size"])
    assert "company_size" in text
    assert "has not refused" in text


def test_handoff_directive_closes_after_a_decline():
    text = handoff_directive(["company_size"], just_declined=True)
    assert "company_size" not in text
    assert "ask no field" in text


def test_declined_close_names_the_handoff_state():
    text = handoff_directive(["company_size"], just_declined=True)
    assert "with the sales team" in text
    assert "same request" in text


def test_declined_close_after_handoff_mentions_updates():
    text = handoff_directive(["company_size"], just_declined=True, already_stated=True)
    assert "do not repeat the hand-off" in text
    assert "will update it" in text


def test_pre_sync_declined_close_never_claims_sent():
    text = next_ask_directive(None, ["company_size"], just_declined=True)
    assert "saved" in text
    assert "Do not say it was sent" in text


def test_failed_sync_directive_is_truthful():
    assert "saved but not yet sent" in SYNC_FAILED_DIRECTIVE
    assert "Never say sent" in SYNC_FAILED_DIRECTIVE
    assert "Ask no further field" in SYNC_FAILED_DIRECTIVE


def test_handoff_after_statement_requires_update_wording_not_a_bare_ack():
    text = handoff_directive(["project_location"], already_stated=True)
    assert "added to the same request" in text
    assert "never reply with a bare acknowledgement" in text


def test_declined_close_after_statement_pins_the_close_sentence():
    text = handoff_directive(
        ["project_location"], already_stated=True, just_declined=True
    )
    assert "exact sentence" in text
    assert "anything you add later will update it" in text
    assert "ask no field" in text
