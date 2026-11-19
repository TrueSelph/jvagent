"""Tests for leadgen validators."""

import pytest

from jvagent.action.leadgen.validators import (
    locale_validator,
    run_validator,
    validate_date,
    validate_email,
    validate_enum,
    validate_number,
    validate_person_name,
    validate_phone_e164,
    validate_phone_gy,
    validate_regex,
)


def test_validate_email_normalizes():
    val, err = validate_email("Jane@Example.COM")
    assert err is None
    assert val == "jane@example.com"


def test_validate_email_rejects_na_without_at():
    """Decline text N/A is handled by the engine decline path, not the validator."""
    val, err = validate_email("N/A")
    assert val is None
    assert err is not None


def test_validate_phone_e164():
    val, err = validate_phone_e164("5551234567")
    assert err is None
    assert val == "+5551234567"


def test_validate_phone_gy_seven_digit():
    val, err = validate_phone_gy("6001234")
    assert err is None
    assert val == "+592 600 1234"


def test_validate_person_name():
    val, err = validate_person_name("Jane Doe")
    assert err is None
    assert val == "Jane Doe"


def test_locale_validator_resolves_builtin():
    assert locale_validator("GY") == "phone_gy"
    assert locale_validator("e164") == "phone_e164"
    assert locale_validator("ZZ") == ""
    assert locale_validator("") == ""


def test_validate_person_name_rejects_too_many_words():
    val, err = validate_person_name("One Two Three Four Five")
    assert val is None
    assert "too many words" in err


def test_validate_person_name_keeps_legitimate_names():
    for raw in ("Anne-Marie Jodhan", "Sonya Oodit", "Test User", "Oodit"):
        val, err = validate_person_name(raw)
        assert err is None
        assert val == raw


def test_validate_enum():
    val, err = validate_enum("MEDIUM", choices=["low", "medium", "high"])
    assert err is None
    assert val == "medium"
    val, err = validate_enum("urgent", choices=["low", "medium", "high"])
    assert val is None
    assert err is not None


def test_validate_regex():
    val, err = validate_regex("AB-12", pattern=r"[A-Z]{2}-\d{2}")
    assert err is None
    assert val == "AB-12"
    val, err = validate_regex("ab-12", pattern=r"[A-Z]{2}-\d{2}")
    assert val is None


def test_validate_number_range():
    val, err = validate_number("3.5", min=1, max=5)
    assert err is None
    assert val == "3.5"
    val, err = validate_number("10", min=1, max=5)
    assert val is None


def test_validate_date_iso():
    val, err = validate_date("2026-11-13")
    assert err is None
    assert val == "2026-11-13"
    val, err = validate_date("13/11/2026", formats=["%d/%m/%Y"])
    assert err is None
    assert val == "2026-11-13"


def test_unknown_validator_errors():
    val, err = run_validator("not_a_real_validator", "x")
    assert val is None
    assert "unknown validator" in err


def test_parse_rejects_unknown_validator():
    from jvagent.action.leadgen.spec import parse_leadgen_spec

    with pytest.raises(ValueError, match="unknown"):
        parse_leadgen_spec(
            {"fields": [{"key": "x", "validator": "emial"}]},
            source_dir="/tmp",
            default_name="t",
        )
