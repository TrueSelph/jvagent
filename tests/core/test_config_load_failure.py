"""``load_app_config`` error visibility and strict mode."""

import pytest


def test_load_app_config_invalid_yaml_logs_warning(tmp_path, caplog):
    from jvagent.core.config import load_app_config

    (tmp_path / "app.yaml").write_text("app: [\n", encoding="utf-8")
    caplog.set_level("WARNING")

    cfg = load_app_config(str(tmp_path))
    assert cfg == {}
    assert "Could not load app.yaml config" in caplog.text


def test_load_app_config_strict_reraises(tmp_path, monkeypatch):
    from jvagent.core.config import load_app_config

    monkeypatch.setenv("JVAGENT_STRICT_CONFIG", "true")
    (tmp_path / "app.yaml").write_text("app: [\n", encoding="utf-8")

    with pytest.raises(Exception):
        load_app_config(str(tmp_path))
