"""Tests for ``coactivate-with`` skill expansion (cockpit feature B)."""

from __future__ import annotations

from pathlib import Path

from jvagent.action.cockpit.catalog.skill_catalog import SkillCatalog
from jvagent.scaffold.skill_resolve import parse_skill_bundle


def _write_bundle(tmp_path: Path, name: str, body: str) -> Path:
    bundle = tmp_path / name
    bundle.mkdir()
    (bundle / "SKILL.md").write_text(body)
    return bundle


def test_parse_coactivate_with_list(tmp_path: Path) -> None:
    bundle = _write_bundle(
        tmp_path,
        "alpha",
        "---\nname: alpha\ndescription: test\ncoactivate-with:\n  - beta\n  - gamma\n---\nbody\n",
    )
    data = parse_skill_bundle(bundle, source="agent")
    assert data is not None
    assert data["coactivate_with"] == ["beta", "gamma"]


def test_parse_coactivate_with_omitted(tmp_path: Path) -> None:
    bundle = _write_bundle(
        tmp_path,
        "alpha",
        "---\nname: alpha\ndescription: test\ntags:\n  - foo\n---\nbody\n",
    )
    data = parse_skill_bundle(bundle, source="agent")
    assert data is not None
    assert data["coactivate_with"] == []


def test_parse_coactivate_with_scalar(tmp_path: Path) -> None:
    bundle = _write_bundle(
        tmp_path,
        "alpha",
        "---\nname: alpha\ndescription: test\ncoactivate-with: beta\n---\nbody\n",
    )
    data = parse_skill_bundle(bundle, source="agent")
    assert data is not None
    assert data["coactivate_with"] == ["beta"]


def test_expand_with_companions_basic() -> None:
    cat = SkillCatalog(
        {
            "a": {"coactivate_with": ["b", "c"]},
            "b": {"coactivate_with": ["d"]},
            "c": {},
            "d": {},
        }
    )
    assert cat.expand_with_companions(["a"], max_depth=2) == ["a", "b", "c", "d"]


def test_expand_with_companions_depth_cap() -> None:
    cat = SkillCatalog(
        {
            "a": {"coactivate_with": ["b"]},
            "b": {"coactivate_with": ["c"]},
            "c": {"coactivate_with": ["d"]},
            "d": {},
        }
    )
    assert cat.expand_with_companions(["a"], max_depth=1) == ["a", "b"]
    assert cat.expand_with_companions(["a"], max_depth=2) == ["a", "b", "c"]


def test_expand_with_companions_cycle_safe() -> None:
    cat = SkillCatalog(
        {
            "a": {"coactivate_with": ["b"]},
            "b": {"coactivate_with": ["a"]},
        }
    )
    assert cat.expand_with_companions(["a"], max_depth=5) == ["a", "b"]


def test_expand_with_companions_missing_companion_dropped() -> None:
    cat = SkillCatalog({"a": {"coactivate_with": ["ghost"]}})
    assert cat.expand_with_companions(["a"]) == ["a"]


def test_expand_with_companions_empty_seed() -> None:
    cat = SkillCatalog({"a": {"coactivate_with": ["b"]}, "b": {}})
    assert cat.expand_with_companions([]) == []


def test_expand_with_companions_preserves_seed_order() -> None:
    cat = SkillCatalog(
        {
            "x": {"coactivate_with": ["z"]},
            "y": {},
            "z": {},
        }
    )
    assert cat.expand_with_companions(["x", "y"]) == ["x", "y", "z"]
