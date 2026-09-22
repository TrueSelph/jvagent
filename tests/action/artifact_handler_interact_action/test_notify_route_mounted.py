"""Notify webhook mounts when the action is loaded, not via core."""

from __future__ import annotations

import importlib
import inspect
import os

from starlette.testclient import TestClient

MINIMAL_APP_YAML = """
app: jvagent_notify_route_test
context:
  name: Notify Route Test
  description: artifact_handler notify mount
config:
  database:
    type: json
    path: ./test_jvdb
  logging:
    enabled: false
  server:
    host: 127.0.0.1
    port: 8766
agents: []
"""

NOTIFY_PATH = "/api/artifact_handler_action/notify/n.Agent.test"


def _server(tmp_path, monkeypatch):
    from jvagent.cli.server_config import (
        _set_db_env_from_config,
        create_server_from_config,
    )
    from jvagent.core.app_context import set_app_root

    monkeypatch.setenv(
        "JVSPATIAL_JWT_SECRET_KEY", "test-jwt-secret-key-for-integration-tests"
    )
    monkeypatch.setenv("JVSPATIAL_ENABLE_DEFERRED_SAVES", "false")
    monkeypatch.setenv("JVSPATIAL_DB_TYPE", "json")
    monkeypatch.setenv("JVSPATIAL_DB_PATH", str(tmp_path / "test_jvdb"))
    app_root = str(tmp_path)
    (tmp_path / "app.yaml").write_text(MINIMAL_APP_YAML.strip(), encoding="utf-8")
    set_app_root(app_root)
    _set_db_env_from_config(app_root)
    return create_server_from_config(debug=False, app_root=app_root)


def test_core_embed_endpoints_does_not_import_artifact_handler():
    from jvagent.core import embed_endpoints

    src = inspect.getsource(embed_endpoints)
    assert "artifact_handler" not in src
    assert "remount_artifact_handler_notify_if_app_built" not in dir(embed_endpoints)


def test_notify_route_on_live_app_after_action_import(tmp_path, monkeypatch):
    """Importing the action after get_app() remounts notify (plugin load)."""
    from jvagent.core.app import App
    from jvagent.core.app_context import clear_app_root

    try:
        server = _server(tmp_path, monkeypatch)
        assert "JVSPATIAL_JSONDB_PATH" not in os.environ
        app = server.get_app()
        importlib.import_module("jvagent.action.artifact_handler_interact_action")
        importlib.reload(
            importlib.import_module(
                "jvagent.action.artifact_handler_interact_action.endpoints"
            )
        )
        client = TestClient(app)
        response = client.post(NOTIFY_PATH, json={})
        assert response.status_code != 404, response.text
    finally:
        App.clear_cache()
        clear_app_root()
