"""Notify webhook must be on the live FastAPI app (not registry-only)."""

from __future__ import annotations

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
    # Own DB env keys so _set_db_env_from_config cannot leak json into later tests.
    monkeypatch.setenv("JVSPATIAL_DB_TYPE", "json")
    monkeypatch.setenv("JVSPATIAL_DB_PATH", str(tmp_path / "test_jvdb"))
    app_root = str(tmp_path)
    (tmp_path / "app.yaml").write_text(MINIMAL_APP_YAML.strip(), encoding="utf-8")
    set_app_root(app_root)
    _set_db_env_from_config(app_root)
    return create_server_from_config(debug=False, app_root=app_root)


def test_notify_route_on_live_app_after_create_server(tmp_path, monkeypatch):
    """Eager import in create_server_from_config must land notify on get_app()."""
    from jvagent.core.app import App
    from jvagent.core.app_context import clear_app_root

    try:
        server = _server(tmp_path, monkeypatch)
        assert "JVSPATIAL_JSONDB_PATH" not in os.environ
        client = TestClient(server.get_app())
        response = client.post(NOTIFY_PATH, json={})
        assert response.status_code != 404, response.text
    finally:
        App.clear_cache()
        clear_app_root()


def test_notify_route_remount_if_app_already_built(tmp_path, monkeypatch):
    """Late remount must keep POST notify off 404 when get_app() already ran."""
    from jvagent.core.app import App
    from jvagent.core.app_context import clear_app_root
    from jvagent.core.embed_endpoints import (
        remount_artifact_handler_notify_if_app_built,
    )

    try:
        server = _server(tmp_path, monkeypatch)
        app = server.get_app()
        remount_artifact_handler_notify_if_app_built(server)
        client = TestClient(app)
        response = client.post(NOTIFY_PATH, json={})
        assert response.status_code != 404, response.text
    finally:
        App.clear_cache()
        clear_app_root()
