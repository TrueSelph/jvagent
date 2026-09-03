"""Tests for scaffold path segment validation."""

from pathlib import Path

import pytest

from jvagent.scaffold.path_safe import resolve_under, validate_safe_segment


def test_validate_safe_segment_rejects_traversal():
    with pytest.raises(ValueError):
        validate_safe_segment("../evil")
    with pytest.raises(ValueError):
        validate_safe_segment("foo/bar")
    with pytest.raises(ValueError):
        validate_safe_segment("")


def test_validate_safe_segment_accepts_valid_names():
    assert validate_safe_segment("my_skill") == "my_skill"
    assert validate_safe_segment("bot-1") == "bot-1"


def test_resolve_under_rejects_escape(tmp_path: Path):
    base = tmp_path / "app"
    base.mkdir()
    with pytest.raises(ValueError):
        resolve_under(base, "..", "outside")
