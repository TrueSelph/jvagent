"""Tests for jvagent.action.loader.info_yaml helpers."""

from pathlib import Path

from jvagent.action.loader import info_yaml


def test_extract_action_name_with_slash(tmp_path: Path) -> None:
    pkg = {"name": "jvagent/foo_bar"}
    assert info_yaml.extract_action_name(pkg, tmp_path) == "foo_bar"


def test_extract_action_name_fallback_dir(tmp_path: Path) -> None:
    d = tmp_path / "my_action"
    d.mkdir()
    pkg = {"name": "single"}
    assert info_yaml.extract_action_name(pkg, d) == "single"


def test_has_info_yaml_files_false_empty(tmp_path: Path) -> None:
    assert info_yaml.has_info_yaml_files(tmp_path) is False


def test_has_info_yaml_files_true(tmp_path: Path) -> None:
    sub = tmp_path / "ns" / "act"
    sub.mkdir(parents=True)
    (sub / "info.yaml").write_text("package:\n  name: x/y\n", encoding="utf-8")
    assert info_yaml.has_info_yaml_files(tmp_path) is True


def test_load_info_yaml_minimal(tmp_path: Path) -> None:
    p = tmp_path / "info.yaml"
    p.write_text(
        "package:\n  name: jvagent/t\n  archetype: T\n",
        encoding="utf-8",
    )
    data = info_yaml.load_info_yaml(p)
    assert data is not None
    assert data["package"]["name"] == "jvagent/t"
