"""A1 range quoting for Google Sheets tab names."""

from jvagent.action.spreadsheet.range_utils import (
    compose_a1_range,
    qualify_sheet_title,
    resolve_sheet_title,
)


def test_fab_2026_empty_range_is_quoted():
    assert compose_a1_range("FAB_2026", "") == "'FAB_2026'!A:ZZ"
    assert compose_a1_range("FAB_2026", None) == "'FAB_2026'!A:ZZ"


def test_fab_2026_fragment_is_quoted():
    assert compose_a1_range("FAB_2026", "A1:Z") == "'FAB_2026'!A1:Z"


def test_letter_only_sheet_stays_unquoted():
    assert qualify_sheet_title("Sheet") == "Sheet"
    assert compose_a1_range("Sheet", "A1:C10") == "Sheet!A1:C10"


def test_sheet1_is_quoted():
    assert compose_a1_range("Sheet1", "") == "'Sheet1'!A:ZZ"


def test_space_and_apostrophe_still_quoted():
    assert qualify_sheet_title("FAB 2026") == "'FAB 2026'"
    assert qualify_sheet_title("O'Brien") == "'O''Brien'"


_TABS = ["FAB 2026", "Objections"]


def test_resolve_fab_2026_to_spaced_tab():
    assert resolve_sheet_title("FAB_2026", _TABS) == "FAB 2026"


def test_resolve_objections_case_insensitive():
    assert resolve_sheet_title("OBJECTIONS", _TABS) == "Objections"


def test_resolve_unknown_title_is_none():
    assert resolve_sheet_title("Missing", _TABS) is None


def test_resolve_exact_title_wins():
    assert resolve_sheet_title("FAB 2026", _TABS) == "FAB 2026"
