# jvagent Configuration Reference

This document maps `app.yaml` config paths to environment variables and documents the configuration merge order. Use it to understand where to set each value and which settings are secrets.

## Configuration Priority

**env var > app.yaml config > hardcoded default**

- **app.yaml**: App structure, defaults, agent list. Use `${VAR}` placeholders for secrets — never real values.
- **.env**: Secrets and local overrides. Copy from `.env.example` and fill in values.
- **deploy.yaml**: For jvdeploy (Lambda/K8s). Injects env vars at deploy time. Use Secrets Manager or CI/CD for secrets.

## Config Mapping Reference

### Server

| app.yaml path | Env var | Default |
|---------------|---------|---------|
| `config.server.title` | `JVAGENT_TITLE` | jvagent API |
| `config.server.description` | `JVAGENT_DESCRIPTION` | jvagent Agentive Platform API |
| `config.server.version` | `JVAGENT_VERSION` | (package version) |
| `config.server.host` | `JVAGENT_HOST` | 127.0.0.1 |
| `config.server.port` | `JVAGENT_PORT` | 8000 |

### Database

| app.yaml path | Env var | Default |
|---------------|---------|---------|
| `config.database.type` | `JVSPATIAL_DB_TYPE` | json |
| `config.database.path` | `JVSPATIAL_DB_PATH` | ./jvagent_db |
| `config.database.uri` | `JVSPATIAL_MONGODB_URI` | mongodb://localhost:27017 |
| `config.database.name` | `JVSPATIAL_MONGODB_DB_NAME` | (none) |
| `config.database.table_name` | `JVSPATIAL_DYNAMODB_TABLE_NAME` | (none) |
| `config.database.region` | `JVSPATIAL_DYNAMODB_REGION` | (none) |
| `config.database.endpoint_url` | `JVSPATIAL_DYNAMODB_ENDPOINT_URL` | (none) |
| `config.database.access_key_id` | `AWS_ACCESS_KEY_ID` | (none) |
| `config.database.secret_access_key` | `AWS_SECRET_ACCESS_KEY` | (none) |

### Authentication

| app.yaml path | Env var | Default |
|---------------|---------|---------|
| `config.auth.enabled` | `JVAGENT_AUTH_ENABLED` | true |
| `config.auth.jwt_expire_minutes` | `JVSPATIAL_JWT_EXPIRE_MINUTES` | 60 |
| `config.auth.api_key_management_enabled` | `JVAGENT_API_KEY_MANAGEMENT_ENABLED` | (auth_enabled) |
| `config.auth.api_key_prefix` | `JVAGENT_API_KEY_PREFIX` | jv_ |
| `config.auth.api_key_header` | `JVAGENT_API_KEY_HEADER` | x-api-key |

**Secrets (env only):**

| Env var | Description |
|---------|-------------|
| `JVSPATIAL_JWT_SECRET` / `JVSPATIAL_JWT_SECRET_KEY` | JWT signing secret. Required when auth enabled. |
| `JVAGENT_ADMIN_PASSWORD` | Admin bootstrap password. Required to create initial admin. |
| `JVAGENT_ADMIN_USERNAME` | Admin username (default: admin) |
| `JVAGENT_ADMIN_EMAIL` | Admin email (default: admin@jvagent.example) |

### Interact Endpoint

| app.yaml path | Env var | Default |
|---------------|---------|---------|
| `config.interact.rate_limit_per_minute` | `JVAGENT_INTERACT_RATE_LIMIT_PER_MINUTE` | 60 |
| `config.interact.max_utterance_length` | `JVAGENT_INTERACT_MAX_UTTERANCE_LENGTH` | 2000 (null = unlimited) |

### File Storage

| app.yaml path | Env var | Default |
|---------------|---------|---------|
| `config.file_storage.enabled` | `JVSPATIAL_FILE_STORAGE_ENABLED` | false |
| `config.file_storage.provider` | `JVSPATIAL_FILE_INTERFACE` | local |
| `config.file_storage.root_dir` | `JVSPATIAL_FILES_ROOT_PATH` | .files |
| `config.file_storage.base_url` | `JVSPATIAL_FILE_STORAGE_BASE_URL` | http://localhost:8000 |
| `config.file_storage.max_size` | `JVSPATIAL_FILE_STORAGE_MAX_SIZE` | 104857600 |

### Logging

| app.yaml path | Env var | Default |
|---------------|---------|---------|
| `config.logging.enabled` | `JVAGENT_LOGGING_ENABLED` | true |
| `config.logging.levels` | `JVAGENT_DB_LOGGING_LEVELS` | ERROR,CRITICAL |
| `config.logging.database.type` | `JVAGENT_LOG_DB_TYPE` | (none) |
| `config.logging.database.uri` | `JVAGENT_LOG_DB_URI` | (none) |
| `config.logging.database.name` | `JVAGENT_LOG_DB_NAME` | jvagent_logs |
| `config.logging.database.path` | `JVAGENT_LOG_DB_PATH` | (none) |
| `config.logging.database.table_name` | `JVSPATIAL_LOG_DB_TABLE_NAME` | (none) |
| `config.logging.database.region` | `JVSPATIAL_LOG_DB_REGION` | (none) |
| `config.logging.database.endpoint_url` | `JVSPATIAL_LOG_DB_ENDPOINT_URL` | (none) |

### CORS

| app.yaml path | Env var | Default |
|---------------|---------|---------|
| `config.cors.enabled` | `JVSPATIAL_CORS_ENABLED` | true |
| `config.cors.origins` | `JVSPATIAL_CORS_ORIGINS` | (comma-separated list) |

### Development

| app.yaml path | Env var | Default |
|---------------|---------|---------|
| `config.development.debug` | `JVSPATIAL_DEBUG` | false |

### API

| app.yaml path | Env var | Default |
|---------------|---------|---------|
| `config.api.graph_endpoint_enabled` | `JVSPATIAL_GRAPH_ENDPOINT_ENABLED` | false |

### Performance (config.performance)

| app.yaml path | Env var | Default |
|---------------|---------|---------|
| `config.performance.enable_profiling` | `JVAGENT_ENABLE_PROFILING` | false |
| `config.performance.enable_agent_caching` | `JVAGENT_ENABLE_AGENT_CACHING` | true |
| `config.performance.agent_cache_ttl` | `JVAGENT_AGENT_CACHE_TTL` | 300 |
| `config.performance.enable_action_cache` | `JVAGENT_ENABLE_ACTION_CACHE` | true |
| `config.performance.action_cache_ttl` | `JVAGENT_ACTION_CACHE_TTL` | 60 |
| `config.performance.enable_deferred_saves` | `JVSPATIAL_ENABLE_DEFERRED_SAVES` | true |
| `config.performance.cache_cleanup_probability` | `JVAGENT_CACHE_CLEANUP_PROBABILITY` | 0.1 |
| `config.performance.enable_interact_router_cache` | `JVAGENT_ENABLE_INTERACT_ROUTER_CACHE` | false |
| `config.performance.interact_router_cache_ttl` | `JVAGENT_INTERACT_ROUTER_CACHE_TTL` | 45 |

## app.yaml-Only Config (No Env Override)

These settings are structural or app-specific and are only configurable in app.yaml:

| app.yaml path | Description |
|---------------|-------------|
| `config.auth.exempt_paths` | List of paths to merge with default auth-exempt paths |
| `config.paths.agents` | Path to agents directory (default: ./agents) |
| `agents` | List of agents to install (required; no env equivalent) |

## Placeholder Resolution

app.yaml supports `${VAR_NAME}` placeholders. At load time, these are resolved from `os.environ`. Use for secrets in app.yaml:

```yaml
config:
  database:
    type: dynamodb
    access_key_id: ${AWS_ACCESS_KEY_ID}
    secret_access_key: ${AWS_SECRET_ACCESS_KEY}
```

Never put real secret values in app.yaml — use placeholders and set the actual values in .env or deploy injection.
