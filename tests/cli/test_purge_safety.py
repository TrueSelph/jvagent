"""--purge target safety + confirmation guard (AUDIT-cli MEDIUM).

--purge rmtree's whatever the resolved DB/log paths point at. A misconfigured
``database.path: "."`` must not delete the app tree, and an interactive
confirmation is required unless assume_yes is set."""

from __future__ import annotations

from pathlib import Path

from jvagent.cli.server import _unsafe_purge_reason, purge_app_data


def test_app_root_itself_is_unsafe(tmp_path):
    assert _unsafe_purge_reason(tmp_path, tmp_path) == "the app root directory"


def test_ancestor_of_app_root_is_unsafe(tmp_path):
    app_root = tmp_path / "app"
    app_root.mkdir()
    reason = _unsafe_purge_reason(tmp_path, app_root)
    assert reason and "ancestor" in reason


def test_filesystem_root_is_unsafe(tmp_path):
    root = Path(tmp_path.anchor)
    assert _unsafe_purge_reason(root, tmp_path) == "filesystem root"


def test_db_path_under_app_root_is_safe(tmp_path):
    app_root = tmp_path / "app"
    app_root.mkdir()
    db = app_root / ".jvagent" / "db.json"
    assert _unsafe_purge_reason(db, app_root) is None


def test_purge_refuses_unsafe_target(tmp_path, monkeypatch):
    """A DB path resolving to the app root must not be deleted."""
    app_root = tmp_path / "app"
    app_root.mkdir()
    sentinel = app_root / "app.yaml"
    sentinel.write_text("app: demo\n")

    # Force resolve_db_path to return the app root itself (database.path: ".").
    import jvagent.cli.server as server_mod

    monkeypatch.setattr(server_mod, "load_app_config", lambda root: {})
    monkeypatch.setattr(
        server_mod, "get_config_value", lambda cfg, key, env, default: "json"
    )
    monkeypatch.setattr(server_mod, "normalize_empty", lambda v: v)
    monkeypatch.setattr(server_mod, "resolve_db_path", lambda *a, **k: str(app_root))
    monkeypatch.setattr(server_mod, "effective_log_db_type", lambda cfg: "json")
    monkeypatch.setattr(server_mod, "resolve_log_db_path", lambda *a, **k: "")
    monkeypatch.setattr(server_mod, "resolve_pageindex_purge_path", lambda *a, **k: "")

    purge_app_data(app_root=str(app_root), assume_yes=True)

    # App sources survived — the unsafe target was refused.
    assert sentinel.exists()
    assert app_root.exists()


def test_purge_aborts_without_confirmation(tmp_path, monkeypatch):
    """Without assume_yes and a 'no' answer, nothing is deleted."""
    app_root = tmp_path / "app"
    app_root.mkdir()
    db_dir = tmp_path / "data"
    db_dir.mkdir()
    (db_dir / "db.json").write_text("{}")

    import jvagent.cli.server as server_mod

    monkeypatch.setattr(server_mod, "load_app_config", lambda root: {})
    monkeypatch.setattr(
        server_mod, "get_config_value", lambda cfg, key, env, default: "json"
    )
    monkeypatch.setattr(server_mod, "normalize_empty", lambda v: v)
    monkeypatch.setattr(server_mod, "resolve_db_path", lambda *a, **k: str(db_dir))
    monkeypatch.setattr(server_mod, "effective_log_db_type", lambda cfg: "json")
    monkeypatch.setattr(server_mod, "resolve_log_db_path", lambda *a, **k: "")
    monkeypatch.setattr(server_mod, "resolve_pageindex_purge_path", lambda *a, **k: "")
    monkeypatch.setattr("builtins.input", lambda *a, **k: "no")

    purge_app_data(app_root=str(app_root), assume_yes=False)

    assert db_dir.exists()  # aborted → survived
