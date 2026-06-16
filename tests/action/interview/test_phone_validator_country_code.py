"""Builtin `phone` validator: country_code prepending for bare local numbers."""

from __future__ import annotations

from jvagent.action.interview.validators import validate_phone


def test_country_code_prepended_to_bare_local_number():
    # 7-digit Guyana local number → country code added → full 10-digit.
    assert validate_phone("600 1234", country_code=592)["value"] == "5926001234"


def test_country_code_formatted_input_normalized():
    assert validate_phone("+592 600-1234", country_code=592)["value"] == "5926001234"


def test_number_already_carrying_country_code_is_unchanged():
    assert validate_phone("5926001234", country_code=592)["value"] == "5926001234"


def test_full_length_number_not_starting_with_code_is_kept():
    # Already a full 10-digit number → not treated as local, no prepend.
    assert validate_phone("6001234567", country_code=592)["value"] == "6001234567"


def test_too_short_even_with_country_code_is_rejected():
    assert validate_phone("123", country_code=592)["valid"] is False


def test_only_local_or_full_length_accepted_nothing_else():
    # With country_code=592: accept a 7-digit local (→ 10) or a 10-digit full;
    # reject every other length.
    assert validate_phone("6001234", country_code=592)["valid"] is True  # 7 → 10
    assert validate_phone("6001234567", country_code=592)["valid"] is True  # 10
    for bad in ("600123", "60012345", "600123456", "60012345678"):  # 6, 8, 9, 11
        assert validate_phone(bad, country_code=592)["valid"] is False, bad


def test_country_code_accepts_string_arg():
    assert validate_phone("6001234", country_code="592")["value"] == "5926001234"


def test_no_country_code_behaviour_unchanged():
    assert validate_phone("6001234567")["value"] == "6001234567"
    assert validate_phone("600 1234")["valid"] is False  # 7 digits, no code → invalid
    assert validate_phone("600 1234", exact_length=7)["value"] == "6001234"
