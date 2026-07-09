# jvagent Configuration Reference

This document defines how to split configuration between `app.yaml` and environment variables for jvagent apps.

For complete canonical key inventory (including integrations and relevant `JVSPATIAL_*` keys), see [environment-keys-reference.md](environment-keys-reference.md).

## Configuration Priority

**env var > app.yaml config > hardcoded default**

- **Empty env values** are treated as unset for `get_config_value` / `get_performance_config_value`.
- **Booleans** (where coerced): `true`/`false`, `1`/`0`, `yes`/`no`, `on`/`off`.
- **app.yaml** should hold app structure and high-convenience defaults that are safe in git.
- **.env / deploy env** should hold secrets, deploy-specific values, and system-level runtime settings.

## Keep / Move / Deprecate Matrix

This matrix is the recommended baseline for new apps and refreshed templates.

| Area | Keep in `app.yaml` | Move to env-first | Notes |
|------|---------------------|-------------------|-------|
| App identity and graph | `app`, `jvagent`, `context` (name/description/timezone), `agents` | `JVAGENT_APP_ID` (optional override) | `agents` remains descriptor-only. |
| Server | `config.server.title`, `description`, `version`, `docs_url`, `redoc_url` | `JVAGENT_HOST`, `JVAGENT_PORT`, `JVAGENT_PUBLIC_BASE_URL` | Host/port are deploy/runtime concerns. |
| Auth | `config.auth.enabled`, `config.auth.exempt_paths` | `JVSPATIAL_JWT_SECRET_KEY`, admin credentials (`JVAGENT_ADMIN_*`) | Keep non-secret policy in YAML; keep credentials in env. |
| Interact | `config.interact.*` limits | env overrides (`JVAGENT_INTERACT_*`) | Good YAML defaults; easy env override. |
| CORS | `config.cors.enabled`, `config.cors.origins` | env overrides (`JVSPATIAL_CORS_*`) | Keep defaults in repo, override per deploy. |
| Performance | `config.performance.*` | env overrides (`JVAGENT_*`, `JVSPATIAL_ENABLE_DEFERRED_SAVES`) | Recommended to keep practical defaults in YAML. |
| DB / Storage / Logging backends | Optional local-dev defaults only | `JVSPATIAL_DB_*`, `JVSPATIAL_FILE_STORAGE_*`, `JVSPATIAL_LOG_DB_*`, AWS creds | Treat as system/deploy configuration for production. |
| Any non-expected key | Not part of the expected-key model | Move to env or remove | Startup flags unexpected keys uniformly. |

## Validation Behavior

`app.yaml` is validated against the expected-key model:

- expected top-level keys: `app`, `version`, `author`, `jvagent`, `context`, `license`, `homepage`, `tags`, `config`, `agents`
- expected `config` sections: `server`, `auth`, `interact`, `cors`, `performance`
- expected keys inside those sections are validated by type where applicable
- any other key path is flagged as an unexpected key at startup

## Prefix Rules

- **`JVSPATIAL_*`**: framework/system concerns (database, JWT, file storage, CORS, graph REST, deferred saves, logging DB, serverless).
- **`JVAGENT_*`**: jvagent app/CLI/runtime controls (server metadata overrides, interact limits, PageIndex naming, profiling/caches).
- **Vendor keys**: action integration secrets (`OPENAI_API_KEY`, `WHATSAPP_*`, `FACEBOOK_*`, etc.).

## Core Mapping (Commonly Used)

### Server

| app.yaml path | Env var | Default |
|---------------|---------|---------|
| `config.server.title` | `JVAGENT_TITLE` | jvagent API |
| `config.server.description` | `JVAGENT_DESCRIPTION` | jvagent Agentive Platform API |
| `config.server.version` | `JVAGENT_VERSION` | (package version) |
| `config.server.host` | `JVAGENT_HOST` | 127.0.0.1 |
| `config.server.port` | `JVAGENT_PORT` | 8000 |
| `config.server.scheduler_enabled` | `JVSPATIAL_SCHEDULER_ENABLED` | auto `true` when any agent installs `jvagent/task_monitor`; else `false` |
| `config.server.scheduler_interval` | `JVSPATIAL_SCHEDULER_INTERVAL` | `1` (scheduler thread poll interval, seconds) |

### Proactive task webhooks (optional)

| Env var | Purpose |
|---------|---------|
| `JVAGENT_TASK_CREATED_WEBHOOK_URL` | Outbound callback when a task is created |
| `JVAGENT_TASK_UPDATED_WEBHOOK_URL` | Outbound callback when a task is updated |
| `JVAGENT_TASK_COMPLETED_WEBHOOK_URL` | Outbound callback when a task completes |
| `JVAGENT_TASK_FAILED_WEBHOOK_URL` | Outbound callback when a task fails |
| `JVAGENT_TASK_CANCELLED_WEBHOOK_URL` | Outbound callback when a task is cancelled |

See [task-tracking.md](task-tracking.md) for the proactive queue and `TaskMonitor` scheduler notes.

### Authentication

| app.yaml path | Env var | Default |
|---------------|---------|---------|
| `config.auth.enabled` | `JVAGENT_AUTH_ENABLED` | true |
| `config.auth.jwt_expire_minutes` | `JVSPATIAL_JWT_EXPIRE_MINUTES` | 60 |
| `config.auth.api_key_management_enabled` | `JVAGENT_API_KEY_MANAGEMENT_ENABLED` | (auth_enabled) |
| `config.auth.api_key_prefix` | `JVAGENT_API_KEY_PREFIX` | jv_ |
| `config.auth.api_key_header` | `JVAGENT_API_KEY_HEADER` | x-api-key |

Secrets (env only):
- `JVSPATIAL_JWT_SECRET_KEY`
- `JVAGENT_ADMIN_PASSWORD`
- `JVAGENT_ADMIN_USERNAME`
- `JVAGENT_ADMIN_EMAIL`

### Interact Endpoint

| app.yaml path | Env var | Default |
|---------------|---------|---------|
| `config.interact.rate_limit_per_minute` | `JVAGENT_INTERACT_RATE_LIMIT_PER_MINUTE` | 60 |
| `config.interact.max_utterance_length` | `JVAGENT_INTERACT_MAX_UTTERANCE_LENGTH` | 2000 |

### CORS

| app.yaml path | Env var | Default |
|---------------|---------|---------|
| `config.cors.enabled` | `JVSPATIAL_CORS_ENABLED` | true |
| `config.cors.origins` | `JVSPATIAL_CORS_ORIGINS` | (comma-separated list) |
| `config.cors.methods` | `JVSPATIAL_CORS_METHODS` | (comma-separated list) |
| `config.cors.headers` | `JVSPATIAL_CORS_HEADERS` | (comma-separated list) |

### Performance

| app.yaml path | Env var | Default |
|---------------|---------|---------|
| `config.performance.enable_profiling` | `JVAGENT_ENABLE_PROFILING` | false |
| `config.performance.enable_agent_cache` | `JVAGENT_ENABLE_AGENT_CACHE` | true |
| `config.performance.agent_cache_ttl` | `JVAGENT_AGENT_CACHE_TTL` | 300 |
| `config.performance.enable_action_cache` | `JVAGENT_ENABLE_ACTION_CACHE` | true |
| `config.performance.action_cache_ttl` | `JVAGENT_ACTION_CACHE_TTL` | 60 |
| `config.performance.enable_deferred_saves` | `JVSPATIAL_ENABLE_DEFERRED_SAVES` | true |
| `config.performance.cache_cleanup_probability` | `JVAGENT_CACHE_CLEANUP_PROBABILITY` | 0.1 |

## Placeholder Resolution

`app.yaml` supports `${VAR_NAME}` placeholders resolved from process environment at load time:

```yaml
config:
  database:
    type: dynamodb
    access_key_id: ${AWS_ACCESS_KEY_ID}
```

Do not commit real secret values in `app.yaml`.

## Related Documentation

- [task-tracking.md](task-tracking.md) - `PROACTIVE` queue, `TaskMonitor`, scheduler bootstrap.
- [proactive-messages.md](proactive-messages.md) - Canned `send_proactive_message` vs queued agentic tasks.
- [environment-keys-reference.md](environment-keys-reference.md) - Canonical env key inventory.
- [integrations-environment.md](integrations-environment.md) - Integration/vendor env keys.
- [scaffolding.md](scaffolding.md) - CLI app and agent scaffolding flow.
