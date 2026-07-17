"""Builtin `digits` validator: strips non-digit symbols, validates remaining digits."""

from __future__ import annotations

from jvagent.action.interview.validators import get_validator, validate_digits


def test_digits_strips_hyphens():
    assert validate_digits("1234-5678")["valid"] is True
    assert validate_digits("1234-5678")["value"] == "12345678"


def test_digits_strips_spaces_and_dots():
    assert validate_digits("12 34.56")["valid"] is True
    assert validate_digits("12 34.56")["value"] == "123456"


def test_digits_strips_letters_and_symbols():
    # "ID-1234" → digits only "1234"
    assert validate_digits("ID-1234")["valid"] is True
    assert validate_digits("ID-1234")["value"] == "1234"


def test_digits_strips_currency_markers():
    assert validate_digits("US $1,234.56")["valid"] is True
    assert validate_digits("US $1,234.56")["value"] == "123456"


def test_digits_rejects_no_digits():
    assert validate_digits("abc")["valid"] is False
    assert validate_digits("---")["valid"] is False


def test_digits_rejects_empty():
    assert validate_digits("")["valid"] is False
    assert validate_digits("   ")["valid"] is False


def test_digits_pure_digits_unchanged():
    assert validate_digits("1234567890")["valid"] is True
    assert validate_digits("1234567890")["value"] == "1234567890"


def test_digits_exact_length_enforced():
    assert validate_digits("1234-5678", exact_length=8)["valid"] is True
    assert validate_digits("1234-5678", exact_length=8)["value"] == "12345678"
    assert validate_digits("123-456", exact_length=8)["valid"] is False


def test_digits_min_length_enforced():
    assert validate_digits("12-34", min_length=4)["valid"] is True
    assert validate_digits("1-2", min_length=4)["valid"] is False


def test_digits_max_length_enforced():
    assert validate_digits("12-34", max_length=4)["valid"] is True
    assert validate_digits("1234-5678", max_length=4)["valid"] is False


def test_digits_registered_in_builtin_validators():
    assert get_validator("digits") is validate_digits
