"""Tests for leadgen validators."""

from jvagent.action.leadgen.validators import (
    validate_email,
    validate_person_name,
    validate_phone_e164,
    validate_phone_gy,
)


def test_validate_email_normalizes():
    val, err = validate_email("Jane@Example.COM")
    assert err is None
    assert val == "jane@example.com"


def test_validate_email_na_sentinel():
    val, err = validate_email("N/A")
    assert err is None
    assert val == "N/A"


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
