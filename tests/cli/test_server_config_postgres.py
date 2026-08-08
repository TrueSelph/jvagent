"""create_server_from_config: PostgreSQL settings reach jvspatial's DatabaseConfig.

Every other backend has its connection settings threaded into the config
object (mongodb its uri/name, dynamodb its table/region). Postgres did not,
so an ``app.yaml`` ``database.uri`` was silently ignored and only the
driver's own env read made a connection possible.
"""

import pytest

# Building a Server with db_type=postgres instantiates PostgresDB, which imports
# asyncpg. It ships in the [test] extra; skip rather than error for anyone
# running the suite without it.
pytest.importorskip("asyncpg")

POSTGRES_APP_YAML = """
app: pg_config_test
context:
  name: test
  description: test
config:
  database:
    type: postgres
  logging:
    enabled: false
  server:
    host: 127.0.0.1
    port: 8765
agents: []
"""

YAML_DSN_APP_YAML = """
app: pg_config_test
context:
  name: test
  description: test
config:
  database:
    type: postgres
    uri: postgresql://yaml:pw@yamlhost:5432/yamldb
    pooler_mode: transaction
    min_pool_size: 3
    max_pool_size: 9
  logging:
    enabled: false
  server:
    host: 127.0.0.1
    port: 8765
agents: []
"""

JSON_APP_YAML = """
app: pg_config_test
context:
  name: test
  description: test
config:
  database:
    type: json
    path: ./test_jvdb
  logging:
    enabled: false
agents: []
"""

_PG_ENV = (
    "JVSPATIAL_POSTGRES_DSN",
    "JVSPATIAL_POSTGRES_POOLER_MODE",
    "JVSPATIAL_POSTGRES_MIN_POOL_SIZE",
    "JVSPATIAL_POSTGRES_MAX_POOL_SIZE",
)


@pytest.fixture
def build_server(tmp_path, monkeypatch):
    """Build a Server from an app.yaml body, with a clean Postgres env."""
    from jvagent.cli.server_config import create_server_from_config
    from jvagent.core.app_context import clear_app_root, set_app_root

    monkeypatch.setenv("JVSPATIAL_JWT_SECRET_KEY", "test-secret-for-pg-config-tests")
    monkeypatch.setenv("JVAGENT_ADMIN_PASSWORD", "x")
    for key in _PG_ENV:
        monkeypatch.delenv(key, raising=False)

    def _build(app_yaml: str):
        app_root = str(tmp_path)
        (tmp_path / "app.yaml").write_text(app_yaml.strip(), encoding="utf-8")
        set_app_root(app_root)
        try:
            return create_server_from_config(debug=False, app_root=app_root)
        finally:
            clear_app_root()

    yield _build


def test_dsn_from_env_reaches_database_config(build_server, monkeypatch):
    monkeypatch.setenv("JVSPATIAL_POSTGRES_DSN", "postgresql://u:pw@envhost:5432/envdb")
    db = build_server(POSTGRES_APP_YAML).config.database
    assert db.db_type == "postgres"
    assert db.postgres_dsn == "postgresql://u:pw@envhost:5432/envdb"


def test_settings_from_app_yaml_reach_database_config(build_server):
    db = build_server(YAML_DSN_APP_YAML).config.database
    assert db.postgres_dsn == "postgresql://yaml:pw@yamlhost:5432/yamldb"
    assert db.postgres_pooler_mode == "transaction"
    assert db.postgres_min_pool_size == 3
    assert db.postgres_max_pool_size == 9


def test_env_overrides_app_yaml(build_server, monkeypatch):
    """Documented precedence: env var > app.yaml > default."""
    monkeypatch.setenv("JVSPATIAL_POSTGRES_DSN", "postgresql://u:pw@envhost:5432/envdb")
    monkeypatch.setenv("JVSPATIAL_POSTGRES_MAX_POOL_SIZE", "25")
    db = build_server(YAML_DSN_APP_YAML).config.database
    assert db.postgres_dsn == "postgresql://u:pw@envhost:5432/envdb"
    assert db.postgres_max_pool_size == 25
    # Untouched by env, so the YAML value still applies.
    assert db.postgres_pooler_mode == "transaction"


def test_unset_settings_stay_none_for_driver_defaults(build_server):
    """Omitted settings must not be pinned here — PostgresDB has its own defaults."""
    db = build_server(POSTGRES_APP_YAML).config.database
    assert db.postgres_dsn is None
    assert db.postgres_pooler_mode is None
    assert db.postgres_min_pool_size is None
    assert db.postgres_max_pool_size is None


def test_postgres_settings_ignored_for_other_backends(build_server, monkeypatch):
    """A stray Postgres DSN must not ride along on a json deployment."""
    monkeypatch.setenv("JVSPATIAL_POSTGRES_DSN", "postgresql://u:pw@envhost:5432/envdb")
    db = build_server(JSON_APP_YAML).config.database
    assert db.db_type == "json"
    assert db.postgres_dsn is None


def test_postgresql_alias_is_honored(build_server, monkeypatch):
    monkeypatch.setenv("JVSPATIAL_POSTGRES_DSN", "postgresql://u:pw@envhost:5432/envdb")
    db = build_server(POSTGRES_APP_YAML.replace("type: postgres", "type: postgresql"))
    assert db.config.database.postgres_dsn == "postgresql://u:pw@envhost:5432/envdb"


def test_non_integer_pool_size_is_ignored(build_server, monkeypatch):
    """A typo'd pool size must not crash startup; the driver default applies."""
    monkeypatch.setenv("JVSPATIAL_POSTGRES_DSN", "postgresql://u:pw@envhost:5432/envdb")
    monkeypatch.setenv("JVSPATIAL_POSTGRES_MAX_POOL_SIZE", "not-a-number")
    db = build_server(POSTGRES_APP_YAML).config.database
    assert db.postgres_max_pool_size is None
