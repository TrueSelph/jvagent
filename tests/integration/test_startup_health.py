"""Integration: server config, pre-startup bootstrap, admin user, health route."""

import pytest
from starlette.testclient import TestClient

MINIMAL_APP_YAML = """
app: jvagent_e2e_test
context:
  name: E2E Test App
  description: integration test
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


@pytest.mark.asyncio
async def test_pre_startup_bootstrap_admin_and_health(tmp_path, monkeypatch):
    from jvspatial.api import get_auth_service

    from jvagent.cli.server_config import (
        _set_db_env_from_config,
        create_server_from_config,
        pre_startup_bootstrap,
    )
    from jvagent.core.app import App
    from jvagent.core.app_context import clear_app_root, set_app_root

    monkeypatch.setenv("JVAGENT_ADMIN_PASSWORD", "secret123456")
    monkeypatch.setenv(
        "JVSPATIAL_JWT_SECRET_KEY", "test-jwt-secret-key-for-integration-tests"
    )
    monkeypatch.setenv("JVSPATIAL_ENABLE_DEFERRED_SAVES", "false")

    app_root = str(tmp_path)
    (tmp_path / "app.yaml").write_text(MINIMAL_APP_YAML.strip(), encoding="utf-8")

    set_app_root(app_root)
    try:
        _set_db_env_from_config(app_root)
        server = create_server_from_config(debug=False, app_root=app_root)
        admin_ok = await pre_startup_bootstrap(server, app_root=app_root)
        assert admin_ok is True

        auth = get_auth_service()
        if hasattr(auth, "count_users"):
            assert await auth.count_users() >= 1
        else:
            assert await auth._user_count() >= 1  # type: ignore[attr-defined]

        client = TestClient(server.get_app())
        response = client.get("/health")
        assert response.status_code == 200
    finally:
        App.clear_cache()
        clear_app_root()
