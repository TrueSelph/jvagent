"""CUCS YAML loader smoke tests."""

from pathlib import Path

import pytest

from jvagent.testing.use_case_loader import (
    discover_use_cases,
    load_use_case,
    schema_path,
)

_EXAMPLES = (
    Path(__file__).resolve().parents[2]
    / "jvagent/action/interview/examples/example_account_gating/use-cases"
)


def test_schema_file_exists():
    assert schema_path().is_file()


@pytest.mark.parametrize(
    "path",
    discover_use_cases(_EXAMPLES),
    ids=lambda p: p.stem,
)
def test_example_use_cases_load(path: Path):
    doc = load_use_case(path)
    assert doc["schema"] == "jvagent.use-case/v1"
    assert doc["turns"]
