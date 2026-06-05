"""create_server_from_config: JWT requirement, forbidden DB env cleanup."""

import os

import pytest

MINIMAL_APP_YAML = """
app: env_align_test
context:
  name: test
  description: test
config:
  database:
    type: json
    path: ./test_jvdb
  logging:
    enabled: false
  server:
    host: 127.0.0.1
    port: 8765
agents: []
"""


def test_create_server_requires_jwt_when_auth_enabled(tmp_path, monkeypatch):
    from jvagent.cli.server_config import create_server_from_config
    from jvagent.core.app_context import clear_app_root, set_app_root

    monkeypatch.delenv("JVSPATIAL_JWT_SECRET_KEY", raising=False)
    monkeypatch.setenv("JVAGENT_ADMIN_PASSWORD", "x")
    app_root = str(tmp_path)
    (tmp_path / "app.yaml").write_text(MINIMAL_APP_YAML.strip(), encoding="utf-8")
    set_app_root(app_root)
    try:
        with pytest.raises(ValueError, match="JVSPATIAL_JWT_SECRET_KEY"):
            create_server_from_config(debug=False, app_root=app_root)
    finally:
        clear_app_root()


def test_create_server_pops_forbidden_jsondb_sqlite_env(tmp_path, monkeypatch):
    from jvagent.cli.server_config import create_server_from_config
    from jvagent.core.app_context import clear_app_root, set_app_root

    monkeypatch.setenv("JVSPATIAL_JWT_SECRET_KEY", "test-secret-for-env-align-tests")
    monkeypatch.setenv("JVAGENT_ADMIN_PASSWORD", "x")
    monkeypatch.setenv("JVSPATIAL_JSONDB_PATH", "/stale/jsondb")
    monkeypatch.setenv("JVSPATIAL_SQLITE_PATH", "/stale/sqlite.db")
    app_root = str(tmp_path)
    (tmp_path / "app.yaml").write_text(MINIMAL_APP_YAML.strip(), encoding="utf-8")
    set_app_root(app_root)
    try:
        create_server_from_config(debug=False, app_root=app_root)
        assert "JVSPATIAL_JSONDB_PATH" not in os.environ
        assert "JVSPATIAL_SQLITE_PATH" not in os.environ
    finally:
        clear_app_root()


def test_create_server_reads_cors_methods_headers_from_env(tmp_path, monkeypatch):
    from jvagent.cli.server_config import create_server_from_config
    from jvagent.core.app_context import clear_app_root, set_app_root

    monkeypatch.setenv("JVSPATIAL_JWT_SECRET_KEY", "test-secret-for-env-align-tests")
    monkeypatch.setenv("JVAGENT_ADMIN_PASSWORD", "x")
    monkeypatch.setenv(
        "JVSPATIAL_CORS_HEADERS", "content-type,authorization,x-session-token"
    )
    monkeypatch.setenv("JVSPATIAL_CORS_METHODS", "GET,POST,OPTIONS")
    app_root = str(tmp_path)
    (tmp_path / "app.yaml").write_text(MINIMAL_APP_YAML.strip(), encoding="utf-8")
    set_app_root(app_root)
    try:
        server = create_server_from_config(debug=False, app_root=app_root)
        assert server.config.cors.cors_headers == [
            "content-type",
            "authorization",
            "x-session-token",
        ]
        assert server.config.cors.cors_methods == ["GET", "POST", "OPTIONS"]
    finally:
        clear_app_root()
