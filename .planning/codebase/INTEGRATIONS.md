# External Integrations

**Analysis Date:** 2026-05-06

## APIs & External Services

**Language Models (LLMs):**
- OpenAI - `jvagent/action/model/language/openai/`
  - SDK/Client: `openai>=1.0.0` + `httpx>=0.24.0`
  - Auth: `OPENAI_API_KEY`
  - Capabilities: sync + streaming chat completions, multimodal (text + images), shared config retry policy (`max_retries`, `retry_initial_delay`, `retry_max_delay`, `retry_backoff_multiplier`, `retry_jitter`, `retry_on_status_codes`).
- Anthropic (Claude) - `jvagent/action/model/language/anthropic/`
  - SDK/Client: Native Messages API via `httpx>=0.24.0` (no anthropic SDK)
  - Auth: `ANTHROPIC_API_KEY`
  - Capabilities: sync/stream, tool calling, multimodal input, retry policy.
- OpenRouter - `jvagent/action/model/language/openrouter/`
  - SDK/Client: `httpx>=0.24.0` (OpenAI-compatible REST)
  - Auth: `OPENAI_API_KEY` (OpenRouter token reuses the OpenAI key slot per `docs/integrations-environment.md`)
  - Capabilities: multi-provider routing, multimodal where supported.
- Ollama - `jvagent/action/model/language/ollama/`
  - SDK/Client: `httpx>=0.24.0` (native Ollama chat API)
  - Auth: `OLLAMA_API_KEY` (optional; cloud/hosted), `OLLAMA_API_ENDPOINT`, `OLLAMA_MODEL`
  - Capabilities: sync/stream chat against local or hosted Ollama daemons.

**Embeddings:**
- OpenAI Embeddings - `jvagent/action/model/embedding/openai/` (`openai>=1.0.0`, `OPENAI_API_KEY`).
- OpenRouter Embeddings - `jvagent/action/model/embedding/openrouter/` (`httpx`).
- HuggingFace Inference API - `jvagent/action/model/embedding/huggingface/` (`httpx`); HuggingFace token via action context.
- Ollama Embeddings - `jvagent/action/model/embedding/ollama/` (`httpx`).
- Generic REST embedding - `jvagent/action/model/embedding/generic/` (`httpx`); custom base URL + bearer.

**Speech / Voice:**
- ElevenLabs (TTS) - `jvagent/action/tts_action/elevenlabs/`
  - SDK/Client: `elevenlabs>=1.13.0`
  - Auth: `ELEVENLABS_API_KEY` (also generic `TTS_API_KEY`).
- Deepgram (STT) - `jvagent/action/stt_action/deepgram/`
  - SDK/Client: `deepgram-sdk>=6.0.0`
  - Auth: `DEEPGRAM_API_KEY` (also generic `STT_API_KEY`).

**Video Generation:**
- HeyGen - `jvagent/action/video_generation/`
  - SDK/Client: `httpx>=0.24.0`
  - Auth: HeyGen API key configured via action context.

**Messaging Channels:**
- WhatsApp (multi-provider) - `jvagent/action/whatsapp/`
  - SDK/Client: `aiohttp>=3.8.0`, `filetype>=1.2.0`, `python-dotenv>=0.19.0`
  - Auth: `WHATSAPP_API_KEY`, `WHATSAPP_TOKEN`, `WHATSAPP_SESSION`
  - Endpoints: `WHATSAPP_API_URL`; configurable `WHATSAPP_REQUEST_TIMEOUT`, `WHATSAPP_SESSION_REGISTER_TIMEOUT_SECONDS`, `WHATSAPP_SKIP_STARTUP_REGISTRATION`.
  - Inbound: webhook adapter (`whatsapp_adapter.py`, `webhook_auth.py`, `whatsapp_filter.py`, `whatsapp_voice_filter.py`).
- Facebook / Messenger - `jvagent/action/facebook_action/`
  - SDK/Client: `requests>=2.28.0` (Graph API direct calls)
  - Auth: `FACEBOOK_APP_SECRET`, `FACEBOOK_APP_ID`, `FACEBOOK_ACCESS_TOKEN`, `FACEBOOK_PAGE_ACCESS_TOKEN`, `FACEBOOK_VERIFY_TOKEN`
  - Config: `FACEBOOK_PAGE_ID`, `FACEBOOK_GRAPH_BASE`, `FACEBOOK_GRAPH_VERSION` (default `v25.0`), `FACEBOOK_WEBHOOK_FIELDS`, `MESSENGER_MESSAGE_WINDOW`, `FACEBOOK_RELOAD_WEBHOOK_SUBSCRIBE`, `FACEBOOK_SKIP_STARTUP_WEBHOOK_REGISTRATION`, `FACEBOOK_WEBHOOK_REGISTER_DELAY_SECONDS`.
- Email (multi-provider) - `jvagent/action/email_action/`
  - SDK/Client: `httpx>=0.27.0` (SendGrid REST), Gmail/Outlook delegated to Google/Microsoft actions.
  - Providers:
    - Gmail (via `jvagent/google_gmail_action`): OAuth2 with `GOOGLE_CLIENT_SECRETS_JSON`.
    - Outlook (via `jvagent/microsoft_outlook_mail_action`): OAuth2 with `MICROSOFT_CLIENT_ID`; inbound uses OData `outlook_mail_filter` on Inbox.
    - SendGrid: `SENDGRID_API_KEY`, `SENDGRID_API_BASE_URL` (default `https://api.sendgrid.com/v3`), `SENDGRID_FROM_EMAIL`, `SENDGRID_FROM_NAME`.
  - Sender override: `EMAIL_DEFAULT_SENDER`, `EMAIL_DEFAULT_SENDER_NAME`.

**Productivity / Workspace - Google (OAuth 2.0, `google-api-python-client>=2.192.0`, `google-auth-httplib2`, `google-auth-oauthlib`):**
- Google Calendar - `jvagent/action/google/google_calendar_action/`
- Google Gmail - `jvagent/action/google/google_gmail_action/`
- Google Drive - `jvagent/action/google/google_drive_action/`
- Google Docs - `jvagent/action/google/google_docs_action/`
- Google Sheets - `jvagent/action/google/google_sheets_action/`
- Auth: `GOOGLE_CLIENT_SECRETS_JSON` (path or inline JSON).
- OAuth callback endpoints: registered in `jvagent/action/google/endpoints.py` (lazily imported in `cli/server_config.py::_import_core_endpoint_modules`).

**Productivity / Workspace - Microsoft Graph (OAuth 2.0, `httpx>=0.24.0`):**
- Microsoft Outlook Mail - `jvagent/action/microsoft/microsoft_outlook_mail_action/`
- Microsoft Outlook Calendar - `jvagent/action/microsoft/microsoft_outlook_calendar_action/`
- Microsoft OneDrive - `jvagent/action/microsoft/microsoft_onedrive_action/`
- Microsoft Excel - `jvagent/action/microsoft/microsoft_excel_action/` (`+ openpyxl>=3.1.0`)
- Auth: `MICROSOFT_CLIENT_ID`.

**Web Search:**
- Brave Search - `jvagent/action/web_search/brave/` (`httpx>=0.24.0`); auth via action context (`BRAVE_API_KEY`).
- SerpAPI - `jvagent/action/web_search/serpapi/` (`google-search-results>=2.4.2`); auth via action context (`SERPAPI_API_KEY`).
- Serper - `jvagent/action/web_search/serper/` (uses transitive `httpx`); auth `SERPER_API_KEY`.

**Social / Marketing:**
- Postiz - `jvagent/action/postiz_action/`
  - SDK/Client: `httpx>=0.24.0`
  - Auth: Postiz API token via action context.
  - Capabilities: cross-platform social media post management.

**Document Indexing / RAG:**
- PageIndex - `jvagent/action/pageindex/pageindex_action/`
  - SDK/Client: `litellm>=1.82.0`, `openai>=1.0.0`, `PyPDF2>=3.0.1`, `pymupdf>=1.26.0`, `tiktoken>=0.11.0`, `python-dotenv`, `pyyaml`
  - Strategy: vectorless RAG via LLM `tree_search` (model id from `PAGEINDEX_TREE_SEARCH_MODEL`, default `gpt-4o-mini`).
  - Auth: `OPENAI_API_KEY` (tree search), Docling stack optional (`docling>=2.0.0`, `tabulate>=0.9.0`, `rapidocr>=3.3,<4`).
- PageIndex Google Drive Sync - `jvagent/action/pageindex/pageindex_google_drive_sync_action/`
  - Combines Google Drive OAuth (`google-api-python-client`, `google-auth-*`) with PageIndex ingest.
- jvforge (delegated PageIndex ingest) - external service
  - Endpoint: `JVAGENT_JVFORGE_BASE_URL` (e.g. `http://127.0.0.1:8088`); ingest POSTs to `POST /v1/process` (sync) or `POST /v1/jobs` (async, when `JVAGENT_JVFORGE_ASYNC=true`).
  - Auth: `JVAGENT_JVFORGE_API_KEY` or `JVFORGE_API_KEY` (sent as `X-API-Key`).
  - Returns artifacts via `llm_webhook_url` callback to jvagent (relies on `JVAGENT_PUBLIC_BASE_URL`).

**Vector Search:**
- Typesense - `jvagent/action/vectorstore/typesense/`
  - SDK/Client: `typesense>=0.21.0`
  - Auth: `TYPESENSE_API_KEY`
  - Use: semantic search VectorStore implementation.

**Model Context Protocol (MCP):**
- `jvagent/action/mcp/`
  - SDK/Client: `mcp>=1.0,<2` (Python 3.10+)
  - Singleton gateway action managing one or more named MCP servers; exposes `fulfill(natural_language_command)` for skill/InteractAction consumption.
  - Filesystem sandbox: `MCP_FILESYSTEM_SANDBOX_MODE`, `MCP_FILESYSTEM_SANDBOX_ROOT`, `MCP_FILESYSTEM_SANDBOX_USER_SCOPED`, `MCP_FILESYSTEM_SANDBOX_DEFAULT_USER` (paths under `<files_root>/<agentId>/<userId>/`).

## Data Storage

**Primary Database (`jvspatial`-managed; selected via `JVSPATIAL_DB_TYPE`):**
- `json` (default) - File-backed JSON store at `JVSPATIAL_DB_PATH` (default `./jvagent_db`).
- `sqlite` - SQLite at `JVSPATIAL_DB_PATH`.
- `mongodb` - Connection: `JVSPATIAL_MONGODB_URI` (default `mongodb://localhost:27017`), DB name `JVSPATIAL_MONGODB_DB_NAME` (default `jvagent_db`), pool `JVSPATIAL_MONGODB_MAX_POOL_SIZE` / `MIN_POOL_SIZE`.
- `dynamodb` - Table `JVSPATIAL_DYNAMODB_TABLE_NAME`, region `JVSPATIAL_DYNAMODB_REGION`, optional `JVSPATIAL_DYNAMODB_ENDPOINT_URL`, AWS credentials via standard `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`.

**Logging Database (separate, `JVSPATIAL_LOG_DB_*`):**
- Levels (`JVSPATIAL_DB_LOGGING_LEVELS`): default `INTERACTION,ERROR,CRITICAL`.
- Backends: `mongodb` (`JVSPATIAL_LOG_DB_URI`, `JVSPATIAL_LOG_DB_NAME`, default `jvagent_logs`) or `dynamodb` (`JVSPATIAL_LOG_DB_TABLE_NAME`, `JVSPATIAL_LOG_DB_REGION`, `JVSPATIAL_LOG_DB_ENDPOINT_URL`).
- Retention: `JVSPATIAL_LOG_RETENTION_DEFAULT_DAYS` (default 60; 0 = indefinite).

**PageIndex Database (per-app, separate from primary):**
- `JVAGENT_PAGEINDEX_DB_TYPE` (json/mongodb/dynamodb), `JVAGENT_PAGEINDEX_DB_PATH` / `_URI` / `_NAME` / `_TABLE_NAME` / `_REGION` / `_ROOT`.
- Default DB name: `{app_id}_pageindex_db` (one DB per app, agents share).

**File Storage:**
- Local (default) - `JVSPATIAL_FILES_ROOT_PATH` (default `./.files`); served at `{JVSPATIAL_API_PREFIX}/files/{path}`.
- S3 - `JVSPATIAL_S3_BUCKET_NAME`, `JVSPATIAL_S3_REGION`, `JVSPATIAL_S3_ACCESS_KEY`, `JVSPATIAL_S3_SECRET_KEY`, optional `JVSPATIAL_S3_ENDPOINT_URL`.
- Public read: `JVSPATIAL_FILES_PUBLIC_READ` (default `true`).
- Max size: `JVSPATIAL_FILE_STORAGE_MAX_SIZE` (default 100 MiB).

**Caching:**
- jvspatial entity cache (`JVSPATIAL_CACHE_BACKEND`): `memory` (default) or `redis` (`JVSPATIAL_REDIS_URL`, `JVSPATIAL_REDIS_TTL`).
- Sizes: `JVSPATIAL_CACHE_SIZE` (default 1000), `JVSPATIAL_L1_CACHE_SIZE` (default 500).
- jvagent application caches (`JVAGENT_*`):
  - Agent cache: `JVAGENT_ENABLE_AGENT_CACHE` (default true), `JVAGENT_AGENT_CACHE_TTL` (default 300s).
  - Action cache: `JVAGENT_ENABLE_ACTION_CACHE` (default true), `JVAGENT_ACTION_CACHE_TTL` (default 60s).
  - DSPy cache: `JVAGENT_ENABLE_DSPY_CACHE` (default false).
  - Interact router cache: `JVAGENT_ENABLE_INTERACT_ROUTER_CACHE` (default false), `JVAGENT_INTERACT_ROUTER_CACHE_TTL` (default 45s).

**Distributed Locks (multi-worker production):**
- Redis (`redis>=5.0.0`): `JVAGENT_CONVERSATION_LOCK_REDIS_URL`.
- DynamoDB (`boto3>=1.28.0`): `JVAGENT_CONVERSATION_LOCK_DYNAMODB_TABLE`.
- Work-claim lease TTL: `JVSPATIAL_WORK_CLAIM_STALE_SECONDS` (default 600).

## Authentication & Identity

**Server Auth (jvspatial):**
- JWT-based authentication (`JVAGENT_AUTH_ENABLED`, default `true`).
- JWT signing: `JVSPATIAL_JWT_SECRET_KEY` (REQUIRED; startup raises `ValueError` if empty + auth enabled).
- Token expiry: `JVSPATIAL_JWT_EXPIRE_MINUTES` (default 60).
- API Key Management: `JVAGENT_API_KEY_MANAGEMENT_ENABLED` (default = auth enabled), prefix `JVAGENT_API_KEY_PREFIX` (default `jv_`), header `JVAGENT_API_KEY_HEADER` (default `x-api-key`).
- Bootstrap admin: `JVAGENT_ADMIN_USERNAME` (default `admin`), `JVAGENT_ADMIN_PASSWORD`, `JVAGENT_ADMIN_EMAIL` (default `<username>@jvagent.example`).
- Default exempt paths: `/health`, `/docs`, `/redoc`, `/openapi.json`, `/favicon.ico`, `/api/auth/{register,login,refresh,logout}`, `/api/agents/*/interact`. App-specific exempt paths via `auth.exempt_paths` in `app.yaml`.
- Roles: `admin` (`*` permission), `user`, `system` (configured in `cli/server_config.py::create_server_from_config`).

**Action-level RBAC:**
- `jvagent/access_control_action` (`AccessControlAction`, singleton) - role-based access control with user permission validation for secure agent operations (`jvagent/action/access_control/`).

**OAuth 2.0 (per-integration):**
- Google: `GOOGLE_CLIENT_SECRETS_JSON` (path or inline JSON), redirect URIs derived from `JVAGENT_PUBLIC_BASE_URL`. Endpoints in `jvagent/action/google/endpoints.py`.
- Microsoft: `MICROSOFT_CLIENT_ID` for Microsoft Graph OAuth.

## Monitoring & Observability

**Error Tracking:**
- Internal jvspatial logging database with `INTERACTION_LEVEL` custom level (`jvagent/logging/service.py`).
- DBLogHandler installed automatically (`jvspatial.logging.config.initialize_logging_database`).
- No external error tracking SaaS (Sentry/Rollbar/etc.) detected.

**Logs:**
- `configure_standard_logging` from `jvspatial.logging` (color output, preserves `DBLogHandler` and `StartupLogCounter` handler classes).
- Log level via `JVSPATIAL_LOG_LEVEL` (overridden by `--debug` CLI flag and `app.yaml` `development.debug`).
- Log file paths: `JVSPATIAL_LOG_DB_PATH`, `JVSPATIAL_LOG_DB_NAME`.

**Profiling:**
- `JVAGENT_ENABLE_PROFILING` (default false); reload via `jvagent/core/profiling.py::reload_profiling_config`.

## CI/CD & Deployment

**Hosting:**
- Primary: AWS Lambda (containerized) with Lambda Web Adapter (`Dockerfile.base`).
- Alternative: any ASGI host (`uvicorn`, host/port via `JVAGENT_HOST`/`JVAGENT_PORT`, defaults `127.0.0.1:8000`; container default `0.0.0.0:8080`).
- Lambda env override: `JVAGENT_PORT=8080`, `JVAGENT_HOST=0.0.0.0`, `START_COMMAND="jvagent ."`.

**CI Pipeline:**
- GitHub Actions reference detected (`pyproject.toml` line 32 references `.github/workflows/test-jvagent.yaml`); workflow file not in working tree but referenced.
- Pre-commit hooks (`.pre-commit-config.yaml`): pre-commit-hooks v2.3.0 (yaml/json/trailing-whitespace), black 24.8.0, isort 6.0.0, flake8 6.1.0, mypy v1.10.1, detect-secrets v1.5.0, manual pytest stage.

**Container Build:**
- `Dockerfile.base` clones jvagent + jvspatial from GitHub (`https://github.com/TrueSelph/jvagent` and `https://github.com/TrueSelph/jvspatial`, branch `dev`) into `/var/task/`, installs into venv at `/opt/venv`, validates `jvagent --help`, removes `git`/`perl-Git`, drops dnf cache.
- App bundling: `jvagent bundle <app_dir>` generates a Dockerfile via `jvagent/cli/commands.py::handle_bundle_command` and `jvagent/bundle/`.

## Environment Configuration

**Required env vars (auth + admin bootstrap, production):**
- `JVSPATIAL_JWT_SECRET_KEY` (32+ bytes, e.g. `openssl rand -hex 32`)
- `JVAGENT_ADMIN_PASSWORD`
- `JVAGENT_PUBLIC_BASE_URL` (for any webhook/OAuth integration)

**Required env vars (per integration as enabled):**
- LLM: `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `OLLAMA_API_KEY` (+ `OLLAMA_API_ENDPOINT`, `OLLAMA_MODEL`)
- Vector store: `TYPESENSE_API_KEY`
- Voice: `ELEVENLABS_API_KEY` / `TTS_API_KEY`, `DEEPGRAM_API_KEY` / `STT_API_KEY`
- Web search: `SERPER_API_KEY`, SerpAPI/Brave keys via action context
- WhatsApp: `WHATSAPP_API_URL`, `WHATSAPP_API_KEY`, `WHATSAPP_SESSION`, `WHATSAPP_TOKEN`
- Facebook: `FACEBOOK_APP_SECRET`, `FACEBOOK_APP_ID`, `FACEBOOK_PAGE_ID`, `FACEBOOK_ACCESS_TOKEN`, `FACEBOOK_PAGE_ACCESS_TOKEN`, `FACEBOOK_VERIFY_TOKEN`
- Email: `GOOGLE_CLIENT_SECRETS_JSON` (Gmail) / `MICROSOFT_CLIENT_ID` (Outlook) / `SENDGRID_API_KEY` + `EMAIL_DEFAULT_SENDER`
- jvforge: `JVAGENT_JVFORGE_BASE_URL`, `JVAGENT_JVFORGE_API_KEY` / `JVFORGE_API_KEY`
- AWS (DynamoDB / Lambda): `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION`, `AWS_ACCOUNT_ID`, `AWS_LAMBDA_FUNCTION_NAME`

**Secrets location:**
- `.env` files at app root and CWD (read by `python-dotenv`; never committed - in `.gitignore` per `.env.example` warning).
- Production: environment variables only. `jvagent` enforces env-first secret resolution: secret-like keys in `agent.yaml` `context:` are ignored with a warning (per `docs/integrations-environment.md`).
- Dev safety: `detect-secrets` pre-commit hook scans staged `.py`/`.txt`/`.yaml`/`.json` files.

## Webhooks & Callbacks

**Incoming (jvagent listens):**
- WhatsApp inbound webhook - `jvagent/action/whatsapp/whatsapp_adapter.py`, signature verification in `webhook_auth.py`. URL derived from `JVAGENT_PUBLIC_BASE_URL`.
- Facebook / Messenger webhook - subscribes to fields in `FACEBOOK_WEBHOOK_FIELDS`; verify token `FACEBOOK_VERIFY_TOKEN`; debouncing window `MESSENGER_MESSAGE_WINDOW`.
- Email inbound - Gmail/Outlook inbox fetch (POST) or SendGrid Inbound Parse POST (`jvagent/action/email_action/`).
- Google OAuth callback - `jvagent/action/google/endpoints.py` (registered conditionally in `cli/server_config.py::_import_core_endpoint_modules`).
- Agent interact endpoint - `/api/agents/{agent_id}/interact` (anonymous, in default exempt paths).
- jvforge LLM callback - PageIndex sets `llm_webhook_url` so jvforge can call back to jvagent for LLM tree search.
- jvspatial webhook framework - `JVSPATIAL_WEBHOOK_HMAC_SECRET`, `JVSPATIAL_WEBHOOK_HMAC_ALGORITHM` (default `sha256`), `JVSPATIAL_WEBHOOK_MAX_PAYLOAD_SIZE` (default 1 MiB), `JVSPATIAL_WEBHOOK_IDEMPOTENCY_TTL` (default 3600s), `JVSPATIAL_WEBHOOK_HTTPS_REQUIRED` (default `true`).
- Serverless deferred dispatch - `POST {JVSPATIAL_API_PREFIX}/_internal/deferred` (default `/api/_internal/deferred`); compatible with `AWS_LWA_INVOKE_MODE=RESPONSE_STREAM` and `AWS_LWA_PASS_THROUGH_PATH`.

**Outgoing (jvagent calls out):**
- All LLM/embedding/voice/search/social/workspace integrations enumerated above.
- jvforge ingest - POST to `{JVAGENT_JVFORGE_BASE_URL}/v1/process` (sync) or `/v1/jobs` (async).
- AWS EventBridge scheduler (when `JVSPATIAL_EVENTBRIDGE_SCHEDULER_ENABLED=true`, auto-on for serverless AWS): creates schedules invoking the configured Lambda ARN (`JVSPATIAL_EVENTBRIDGE_LAMBDA_ARN`) in scheduler group `JVSPATIAL_EVENTBRIDGE_SCHEDULER_GROUP` (default `default`); requires `JVSPATIAL_EVENTBRIDGE_ROLE_ARN`.
- TaskDispatcher - `jvagent/action/task_dispatcher/` consumes EventBridge / cron ticks to dispatch proactive tasks; `task_creation_interact_action` and `task_trigger_interact_action` create/fire tasks.
- Channel adapters (WhatsApp send, Facebook reply, Gmail/Outlook send, SendGrid Mail Send v3, ElevenLabs TTS synthesis, etc.).

---

*Integration audit: 2026-05-06*
