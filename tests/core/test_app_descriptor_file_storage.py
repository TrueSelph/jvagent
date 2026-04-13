"""AppDescriptor file_storage fields match get_file_storage_config (Server)."""

from pathlib import Path

import yaml

from jvagent.core.app_loader import AppDescriptor


def test_app_descriptor_uses_config_file_storage_root_when_env_unset(
    tmp_path: Path, monkeypatch
):
    monkeypatch.delenv("JVSPATIAL_FILES_ROOT_PATH", raising=False)
    app_yaml = tmp_path / "app.yaml"
    app_yaml.write_text(
        """
app: test_app
context:
  name: Test
config:
  file_storage:
    root_dir: /from/config/only
""",
        encoding="utf-8",
    )
    data = yaml.safe_load(app_yaml.read_text(encoding="utf-8"))
    desc = AppDescriptor(data, tmp_path)
    assert desc.file_storage_root_dir == "/from/config/only"


def test_app_descriptor_env_overrides_config_root(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("JVSPATIAL_FILES_ROOT_PATH", "/from/env")
    app_yaml = tmp_path / "app.yaml"
    app_yaml.write_text(
        """
app: test_app
context:
  name: Test
  file_storage_root_dir: /from/context/legacy
config:
  file_storage:
    root_dir: /from/config
""",
        encoding="utf-8",
    )
    data = yaml.safe_load(app_yaml.read_text(encoding="utf-8"))
    desc = AppDescriptor(data, tmp_path)
    assert desc.file_storage_root_dir == "/from/env"
