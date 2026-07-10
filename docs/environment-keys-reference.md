# jvagent Environment Keys Reference (Canonical)

This is the single reference for environment keys used by `jvagent`.

- Full framework key definitions live in jvspatial: `jvspatial/docs/md/environment-keys-reference.md`
- Runtime merge behavior is documented in: [configuration.md](configuration.md)
- Integration-specific details are documented in: [integrations-environment.md](integrations-environment.md)

## Placement Guidance (Lean app.yaml)

Use this split for new app descriptors:

- Keep in `app.yaml`: app structure (`app`, `context`, `agents`) plus safe convenience defaults (`config.server` metadata, `config.auth.enabled`, `config.auth.exempt_paths`, `config.interact`, `config.cors`, `config.performance`).
- Keep in env (`.env`, deploy injection, secret manager): secrets, deploy/runtime-specific values, and system/backend configuration.
- If a value differs by environment (local/staging/prod), prefer env even if a YAML fallback exists.

## 1) jvagent Core Keys (`JVAGENT_*`)

### Server and app identity
- `JVAGENT_APP_ID` - App id override.
- `JVAGENT_HOST` - Host bind address.
- `JVAGENT_PORT` - Port bind value.
- `JVAGENT_TITLE` - API title.
- `JVAGENT_DESCRIPTION` - API description.
- `JVAGENT_VERSION` - API version.
- `JVAGENT_PUBLIC_BASE_URL` - Public origin for webhooks, OAuth callbacks, and absolute media URLs.

### Bootstrap/auth app-level controls
- `JVAGENT_ADMIN_PASSWORD` - Initial admin password (required for bootstrap flow).
- `JVAGENT_ADMIN_USERNAME` - Initial admin username.
- `JVAGENT_ADMIN_EMAIL` - Initial admin email.
- `JVAGENT_AUTH_ENABLED` - App-level auth enable toggle.
- `JVAGENT_API_KEY_MANAGEMENT_ENABLED` - API-key management enable toggle.
- `JVAGENT_API_KEY_PREFIX` - API-key prefix.
- `JVAGENT_API_KEY_HEADER` - API-key header name.

### Interact endpoint
- `JVAGENT_INTERACT_RATE_LIMIT_PER_MINUTE` - Interact endpoint rate limit.
- `JVAGENT_INTERACT_MAX_UTTERANCE_LENGTH` - Max utterance length for interact endpoint.
- `JVAGENT_INTERACT_MAX_DATA_JSON_BYTES` - Max serialized size of the control portion of `data` (default 256 KB; media keys validated separately; `none` disables).
- `JVAGENT_INTERACT_MAX_MEDIA_BYTES` - Max serialized size of the media portion of `data` — inline base64 uploads (default 20 MB; `none` disables).
- `JVAGENT_INTERACT_REDACT_DEBUG` - When truthy, the public interact endpoint redacts debug/observability (interaction detail + report) outside production too. Off by default (dev keeps full detail); production always redacts.
- `JVAGENT_INTERACT_PUBLIC_AUTH` - Public-endpoint session auth (ADR-0020): `off` (default, legacy), `log` (observe-only), `required` (enforce 401). Needs `JVSPATIAL_JWT_SECRET_KEY`.
- `JVAGENT_INTERACT_TOKEN_TTL_SECONDS` - Mode B session capability token lifetime (default `604800` = 7 days).

### PageIndex
- `JVAGENT_PAGEINDEX_DB_TYPE` - PageIndex backend type.
- `JVAGENT_PAGEINDEX_DB_PATH` - PageIndex JSON/SQLite path.
- `JVAGENT_PAGEINDEX_DB_NAME` - PageIndex DB name.
- `JVAGENT_PAGEINDEX_DB_ROOT` - Root path when DB path is derived.
- `JVAGENT_PAGEINDEX_DB_URI` - PageIndex MongoDB URI.
- `JVAGENT_PAGEINDEX_DB_TABLE_NAME` - PageIndex DynamoDB table.
- `JVAGENT_PAGEINDEX_DB_REGION` - PageIndex DynamoDB region.

### jvforge (PageIndex delegated ingest)
- `JVAGENT_JVFORGE_BASE_URL` - jvforge service origin; when set, PageIndex ingest is delegated to jvforge.
- `JVAGENT_JVFORGE_API_KEY` / `JVFORGE_API_KEY` - Optional API key for jvforge inbound requests (sent as `X-API-Key`).
- `JVAGENT_JVFORGE_ASYNC` - Toggle async (webhook-callback) ingest vs synchronous.
- `JVAGENT_JVFORGE_ASYNC_HTTP_TIMEOUT` - HTTP timeout (seconds) for async jvforge calls.

See [Integration environment variables](integrations-environment.md#jvforge-pageindex) for the full jvforge flow.

### Proactive task lifecycle webhooks
- `JVAGENT_TASK_CREATED_WEBHOOK_URL` - Outbound webhook on task create.
- `JVAGENT_TASK_UPDATED_WEBHOOK_URL` - Outbound webhook on task update.
- `JVAGENT_TASK_COMPLETED_WEBHOOK_URL` - Outbound webhook on task complete.
- `JVAGENT_TASK_FAILED_WEBHOOK_URL` - Outbound webhook on task fail.
- `JVAGENT_TASK_CANCELLED_WEBHOOK_URL` - Outbound webhook on task cancel.

### Performance and runtime behavior
- `JVAGENT_DISABLE_RUNTIME_PIP_INSTALL` - Disables runtime package installation for action deps.
- `JVAGENT_ENABLE_PROFILING` - Enables profiling.
- `JVAGENT_ENABLE_AGENT_CACHE` - Enables agent cache.
- `JVAGENT_AGENT_CACHE_TTL` - Agent cache TTL.
- `JVAGENT_ENABLE_ACTION_CACHE` - Enables action cache.
- `JVAGENT_ACTION_CACHE_TTL` - Action cache TTL.
- `JVAGENT_ENABLE_DSPY_CACHE` - Enables DSPy cache.
- `JVAGENT_CACHE_CLEANUP_PROBABILITY` - Cache cleanup probability.

## 2) Relevant jvspatial Keys Used by jvagent (`JVSPATIAL_*`)

These are commonly used by `jvagent` and should be configured in `jvagent` deployments.

### Database
- `JVSPATIAL_DB_TYPE`
- `JVSPATIAL_DB_PATH`
- `JVSPATIAL_MONGODB_URI`
- `JVSPATIAL_MONGODB_DB_NAME`
- `JVSPATIAL_MONGODB_MAX_POOL_SIZE`
- `JVSPATIAL_MONGODB_MIN_POOL_SIZE`
- `JVSPATIAL_DYNAMODB_TABLE_NAME`
- `JVSPATIAL_DYNAMODB_REGION`
- `JVSPATIAL_DYNAMODB_ENDPOINT_URL`

### Auth/rate limit (framework side)
- `JVSPATIAL_JWT_SECRET_KEY`
- `JVSPATIAL_JWT_ALGORITHM`
- `JVSPATIAL_JWT_EXPIRE_MINUTES`
- `JVSPATIAL_JWT_REFRESH_EXPIRE_DAYS`
- `JVSPATIAL_AUTH_ENABLED`
- `JVSPATIAL_RATE_LIMIT_ENABLED`
- `JVSPATIAL_RATE_LIMIT_DEFAULT_REQUESTS`
- `JVSPATIAL_RATE_LIMIT_DEFAULT_WINDOW`

### API/server/cors
- `JVSPATIAL_API_PREFIX`
- `JVSPATIAL_GRAPH_ENDPOINT_ENABLED`
- `JVSPATIAL_ENVIRONMENT` - Runtime mode (`development` or `production`) used by jvagent interact response filtering and CLI safety checks.
- `JVSPATIAL_HOST`
- `JVSPATIAL_PORT`
- `JVSPATIAL_LOG_LEVEL`
- `JVSPATIAL_DEBUG`
- `JVSPATIAL_CORS_ENABLED`
- `JVSPATIAL_CORS_ORIGINS`
- `JVSPATIAL_CORS_METHODS`
- `JVSPATIAL_CORS_HEADERS`
- `JVSPATIAL_SCHEDULER_ENABLED` - Enable jvspatial native scheduler (`TaskMonitor` auto-enables when `jvagent/task_monitor` is installed unless overridden).
- `JVSPATIAL_SCHEDULER_INTERVAL` - Scheduler thread poll interval in seconds (default `1`).

### File storage and S3
- `JVSPATIAL_FILE_STORAGE_ENABLED`
- `JVSPATIAL_FILE_STORAGE_PROVIDER`
- `JVSPATIAL_FILES_ROOT_PATH`
- `JVSPATIAL_FILE_STORAGE_BASE_URL`
- `JVSPATIAL_FILE_STORAGE_MAX_SIZE`
- `JVSPATIAL_FILES_PUBLIC_READ`
- `JVSPATIAL_S3_BUCKET_NAME`
- `JVSPATIAL_S3_REGION`
- `JVSPATIAL_S3_ACCESS_KEY`
- `JVSPATIAL_S3_SECRET_KEY`
- `JVSPATIAL_S3_ENDPOINT_URL`

### Logging and cache
- `JVSPATIAL_DB_LOGGING_ENABLED`
- `JVSPATIAL_DB_LOGGING_LEVELS`
- `JVSPATIAL_LOG_DB_TYPE`
- `JVSPATIAL_LOG_DB_URI`
- `JVSPATIAL_LOG_DB_NAME`
- `JVSPATIAL_LOG_DB_PATH`
- `JVSPATIAL_LOG_DB_TABLE_NAME`
- `JVSPATIAL_LOG_DB_REGION`
- `JVSPATIAL_LOG_DB_ENDPOINT_URL`
- `JVSPATIAL_LOG_RETENTION_DEFAULT_DAYS`
- `JVSPATIAL_CACHE_BACKEND`
- `JVSPATIAL_CACHE_SIZE`
- `JVSPATIAL_L1_CACHE_SIZE`
- `JVSPATIAL_REDIS_URL`
- `JVSPATIAL_REDIS_TTL`

### Deferred/serverless behavior
- `JVSPATIAL_ENABLE_DEFERRED_SAVES`
- `JVSPATIAL_DEFERRED_TASK_PROVIDER`
- `JVSPATIAL_AWS_DEFERRED_TRANSPORT`
- `JVSPATIAL_AWS_SQS_QUEUE_URL`
- `JVSPATIAL_DEFERRED_INVOKE_DISABLED`
- `JVSPATIAL_DEFERRED_INVOKE_SECRET`
- `JVSPATIAL_WORK_CLAIM_STALE_SECONDS`
- `JVSPATIAL_EVENTBRIDGE_SCHEDULER_ENABLED`
- `JVSPATIAL_EVENTBRIDGE_ROLE_ARN`
- `JVSPATIAL_EVENTBRIDGE_LAMBDA_ARN`
- `JVSPATIAL_EVENTBRIDGE_SCHEDULER_GROUP`
- `JVSPATIAL_LWA_ENV_DEFAULTS`

## 3) Integration and Vendor Keys (non-prefixed)

### Model / search APIs

Model action credentials are resolved from environment variables exclusively
(no ``api_key`` attribute on ``BaseModelAction`` / Ollama). Each provider
falls back to a sibling env var when its primary key is unset:

- `OPENAI_API_KEY` — OpenAI language + embedding actions; also used as a
  fallback by OpenRouter actions.
- `ANTHROPIC_API_KEY` — Anthropic language action.
- `OPENROUTER_API_KEY` — OpenRouter language + embedding actions
  (falls back to `OPENAI_API_KEY`).
- `HUGGINGFACE_API_KEY` / `HF_API_KEY` — HuggingFace embedding action.
- `OLLAMA_API_KEY` — only required for hosted/cloud Ollama deployments;
  local `ollama serve` does not need a key.
- `GENERIC_EMBEDDING_API_KEY` — default env var for the generic embedding
  action; override per-instance via the ``api_key_env`` attribute.
- `PAGEINDEX_TREE_SEARCH_MODEL`
- `SERPER_API_KEY`
- `TYPESENSE_API_KEY`

### WhatsApp
- `WHATSAPP_API_URL`
- `WHATSAPP_API_KEY`
- `WHATSAPP_SESSION`
- `WHATSAPP_TOKEN`
- `WHATSAPP_SESSION_REGISTER_TIMEOUT_SECONDS`
- `WHATSAPP_SKIP_STARTUP_REGISTRATION`
- `WHATSAPP_REQUEST_TIMEOUT`
- Meta Cloud API (`provider: meta`) — **agent.yaml** (preferred): `waba_id`, `phone_number_id`, `access_token` on WhatsAppAction; **env fallback** when yaml empty: `WHATSAPP_WABA_ID`, `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_ACCESS_TOKEN`
- Meta Cloud API — **env**: `WHATSAPP_APP_SECRET` (webhook signature; falls back to `FACEBOOK_APP_SECRET`), `WHATSAPP_APP_ID` (optional; falls back to `FACEBOOK_APP_ID`), `WHATSAPP_GRAPH_VERSION` (default `v25.0`)
- **jvconnect proxy** (recommended for Tech Provider / shared Meta app): set `WHATSAPP_CREDENTIAL_SOURCE=jvconnect` (or `credential_source: jvconnect` on the action), `JVCONNECT_URL`, `JVCONNECT_API_KEY`, and `WHATSAPP_PHONE_NUMBER_ID`. Do **not** set `WHATSAPP_ACCESS_TOKEN` / `WHATSAPP_APP_SECRET`. After startup webhook registration, `JVCONNECT_WEBHOOK_SECRET` is persisted on the action (or set from jvconnect’s register response). Optional: `WHATSAPP_PROXY_URL` alias for `JVCONNECT_URL`, `WHATSAPP_WABA_ID` for scoped admin ops.
- Meta verify token: auto-derived from agent id + app secret (optional `verify_token` on action to override)
- After `jvagent --purge`, agent id changes — update Meta App Dashboard callback URL to match `GET .../meta/webhook-status` `expected_callback_url` (Graph override alone does not update the `application` layer)
- Meta media/voice outbound requires `JVAGENT_PUBLIC_BASE_URL` (files fetched from jvagent before Graph upload)
- Meta typing uses inbound message wamid; configure `stt_action` / `tts_action` on the WhatsApp action for voice notes
- `WHATSAPP_SKIP_STARTUP_WEBHOOK_REGISTRATION` — when `true`, skip deferred Meta webhook override on startup (meta provider only)
- `WHATSAPP_WEBHOOK_REGISTER_DELAY_SECONDS` — optional seconds before Meta Graph override on startup (default `0`; meta provider only)
- `WHATSAPP_RELOAD_WEBHOOK_SUBSCRIBE` — when `false`, skip Meta webhook override on action reload (default subscribe on reload)
- `WHATSAPP_META_WAMID_DEDUP_TTL_SECONDS` — in-process wamid dedup TTL for meta webhooks (default `86400`)
- `WHATSAPP_META_WAMID_DEDUP_MAX` — max wamid dedup cache entries (default `10000`)
- **Voice calls (jvvoice)**: subscribe Meta webhook field `calls`; enable Calling API. Requires `jvagent/whatsapp_voice_action` with `JVVOICE_BASE_URL` + `JVVOICE_API_KEY`, plus a deployed **jvvoice** service. See [`.planning/runbooks/whatsapp-voice-calls.md`](../.planning/runbooks/whatsapp-voice-calls.md).

### jvvoice delegation (WhatsApp voice calls)
- `JVVOICE_BASE_URL` — **jvagent**: public URL of jvvoice connector API (`POST /api/calls/accept`)
- `JVVOICE_API_KEY` — **both**: shared bearer token for jvvoice connector API
- `JVAGENT_PUBLIC_BASE_URL` — **jvagent**: sent to jvvoice as `jvagent_base_url` for `/interact` callbacks

### LiveKit (jvvoice only)
- `LIVEKIT_URL` — LiveKit server WebSocket URL (`wss://…`); set on jvvoice only
- `LIVEKIT_API_KEY` — LiveKit API key (jvvoice only)
- `LIVEKIT_API_SECRET` — LiveKit API secret (jvvoice only)
- `LIVEKIT_AGENT_NAME` — worker registration name on jvvoice (default `jvvoice`; must match `WhatsAppVoiceAction.agent_name`)
- `JVVOICE_API_PORT` — connector API listen port on jvvoice (default `8080`)
- `DEEPGRAM_STT_MODEL` — optional STT model for jvvoice (default `nova-3`)
- `ELEVENLABS_TTS_MODEL` — optional TTS model for jvvoice (default `eleven_flash_v2_5`)
- `ELEVENLABS_VOICE_ID` — optional ElevenLabs voice ID for jvvoice TTS
- `JVVOICE_INTERACT_STREAM` — stream jvagent `/interact` SSE into voice TTS on jvvoice (default `true`)

### Facebook / Messenger
- `FACEBOOK_API_URL`
- `FACEBOOK_APP_SECRET`
- `FACEBOOK_APP_ID`
- `FACEBOOK_PAGE_ID`
- `FACEBOOK_ACCESS_TOKEN`
- `FACEBOOK_VERIFY_TOKEN`
- `FACEBOOK_WEBHOOK_FIELDS`
- `FACEBOOK_PAGE_ACCESS_TOKEN`
- `FACEBOOK_GRAPH_BASE`
- `FACEBOOK_GRAPH_VERSION`
- `MESSENGER_MESSAGE_WINDOW`
- `FACEBOOK_RELOAD_WEBHOOK_SUBSCRIBE`
- `FACEBOOK_SKIP_STARTUP_WEBHOOK_REGISTRATION`
- `FACEBOOK_WEBHOOK_REGISTER_DELAY_SECONDS`

### SendGrid
- `SENDGRID_API_KEY`
- `SENDGRID_API_BASE_URL`
- `SENDGRID_FROM_EMAIL`
- `SENDGRID_FROM_NAME`

### Speech / media examples
- `TTS_API_KEY`
- `STT_API_KEY`
- `ELEVENLABS_API_KEY`
- `DEEPGRAM_API_KEY`

### Runtime/platform keys frequently present in deployments
- `SERVERLESS_MODE`
- `AWS_LAMBDA_FUNCTION_NAME`
- `AWS_REGION`
- `AWS_DEFAULT_REGION`
- `AWS_ACCOUNT_ID`
- `AWS_ACCESS_KEY_ID`
- `AWS_SECRET_ACCESS_KEY`

## 4) Canonicality Rule

If a key is not listed in this document (or in the linked jvspatial key reference for framework keys), treat it as non-canonical for `jvagent` configuration.

## 5) Migration Notes for Existing app.yaml Files

- `app.yaml` now uses expected-key validation.
- Any key outside the expected model is flagged at startup as unexpected.
- Keep deployment/system/secrets in env keys from this document.
