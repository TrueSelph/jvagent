# External Integrations

**Analysis Date:** 2026-05-06

## APIs & External Services

**Language Models:**
- OpenAI - GPT-4, GPT-3.5-turbo, GPT-4-turbo language models
  - SDK: `openai>=1.0.0`
  - Auth: `OPENAI_API_KEY` environment variable
  - Implementations:
    - `jvagent/action/model/language/openai/` - Full OpenAI integration with streaming support
    - `jvagent/action/model/embedding/openai/` - Text embedding models
  - Models supported: GPT-4 variants, embedding models (text-embedding-3-small/large, ada)

- Anthropic - Claude language models
  - SDK: Native HTTP via httpx (no SDK required in pyproject)
  - Auth: `ANTHROPIC_API_KEY` environment variable
  - Implementation: `jvagent/action/model/language/anthropic/`
  - Features: Native Messages API, tool calling, multimodal input (text + images)

- OpenRouter - Multi-model routing service
  - Auth: `OPENROUTER_API_KEY`
  - Implementation: `jvagent/action/model/language/openrouter/`
  - Provides access to: OpenAI, Anthropic, Meta, Mistral, and other models

- Ollama - Local LLM inference
  - Implementation: `jvagent/action/model/language/ollama/`
  - Models: Local LLM support for on-premise deployment
  - Feature: Streaming support for local models

**Web Search:**
- Brave Search - Privacy-respecting search engine
  - SDK: httpx-based REST API client
  - Auth: `BRAVE_SEARCH_API_KEY`
  - Implementation: `jvagent/action/web_search/brave/brave.py`
  - Endpoints: Search with timeout configuration

- SerpAPI - Search engine results API
  - Auth: `SERPAPI_API_KEY`
  - Implementation: `jvagent/action/web_search/serpapi/`

- Serper - Google search results API
  - Auth: `SERPER_API_KEY`
  - Implementation: `jvagent/action/web_search/serper/`

**Text-to-Speech (TTS):**
- ElevenLabs - AI voice synthesis
  - SDK: `elevenlabs>=1.13.0`
  - Auth: `ELEVENLABS_API_KEY`
  - Implementation: `jvagent/action/tts_action/elevenlabs/`
  - Features: Multiple voices, streaming support

**Speech-to-Text (STT):**
- Deepgram - Speech recognition API
  - SDK: `deepgram-sdk>=6.0.0`
  - Auth: `DEEPGRAM_API_KEY`
  - Implementation: `jvagent/action/stt_action/deepgram/`

**Video Generation:**
- HeyGen - AI video creation from scripts
  - SDK: httpx-based REST API
  - Auth: `HEYGEN_API_KEY`
  - Implementation: `jvagent/action/video_generation/`

**Social Media & Messaging:**
- WhatsApp - Messaging platform
  - SDK: aiohttp for API calls
  - Auth: Multiple provider support (Twilio, Meta)
  - Implementation: `jvagent/action/whatsapp/`
  - Features: Inbound/outbound messages, media handling (file type detection)
  - Location: `jvagent/action/whatsapp/info.yaml`

- Facebook Graph API - Social media platform
  - SDK: httpx for API calls
  - Auth: `FACEBOOK_ACCESS_TOKEN`
  - Implementation: `jvagent/action/facebook_action/`
  - Features: Posts, comments, Messenger webhooks
  - Location: `jvagent/action/facebook_action/info.yaml`

- Postiz - Social media management platform
  - SDK: httpx for API
  - Auth: `POSTIZ_API_KEY`
  - Implementation: `jvagent/action/postiz_action/`
  - Features: Multi-channel social media posting

## Data Storage

**Databases:**
- JSON (default backend)
  - Provider: jvspatial
  - Connection: File-based, path configured via `JVSPATIAL_DB_PATH`
  - Format: Hierarchical JSON document storage
  - Location: Configured in `jvagent/core/app.yaml`

- SQLite (optional backend)
  - Provider: jvspatial
  - Connection: File-based SQLite database
  - Config env var: `JVSPATIAL_DB_PATH` (same as JSON)
  - Selection: Configured via `JVSPATIAL_DB_TYPE=sqlite`

**Vector Storage:**
- Typesense - Vector search engine
  - Client: `typesense` (optional, conditional import)
  - Connection: HTTP client to Typesense server
  - Config:
    - Host: `TYPESENSE_HOST` (default: localhost)
    - Port: `TYPESENSE_PORT` (default: 8108)
    - Auth: `TYPESENSE_API_KEY` environment variable
  - Implementation: `jvagent/action/vectorstore/typesense/typesense.py`
  - Features: Vector search, hybrid search, typo tolerance
  - Embedding dimensions: 384 (default for sentence-transformers)

**File Storage:**
- Local filesystem (default) - `jvagent/core/app.py`
  - Provider: Local directory storage
  - Root: `.files` (configurable via app.yaml or `jvagent/core/app.py`)
  - API: `App.get_file()`, `App.put_file()`, `App.delete_file()`

- Cloud storage (via jvspatial)
  - Supported: S3 and other cloud providers
  - Provider selection: `file_storage_provider` attribute on App node
  - Configuration: App-level settings in database

**Caching:**
- Redis - Distributed conversation locks (optional)
  - Connection: URL-based (redis://...)
  - Env var: `JVAGENT_CONVERSATION_LOCK_REDIS_URL`
  - TTL: `JVAGENT_CONVERSATION_LOCK_TTL_SECONDS` (default: 45 seconds)
  - Implementation: `jvagent/memory/distributed_conversation_lock.py`
  - Use case: Serialize conversation mutations across worker processes

## Authentication & Identity

**OAuth2 Providers:**
- Google OAuth2
  - Implementation: `jvagent/action/google/`
  - Auth file: `GOOGLE_CLIENT_SECRETS_JSON` environment variable
  - Scopes: Gmail, Drive, Sheets, Docs, Calendar
  - Flows: OAuth2 with redirect handling via FastAPI endpoints

- Microsoft OAuth2
  - Implementation: `jvagent/action/microsoft/`
  - Auth: `MICROSOFT_CLIENT_ID`, `MICROSOFT_TENANT_ID`
  - Services: Outlook Mail, Calendar, OneDrive, Excel
  - Flows: OAuth2 with token refresh

**Email Provider Authentication:**
- Gmail - Google OAuth + inbox access
  - Auth: Via Google OAuth2
  - Implementation: `jvagent/action/email_action/` with `GoogleGmailAction`

- Outlook - Microsoft Graph + OAuth2
  - Auth: `MICROSOFT_CLIENT_ID` for OAuth
  - Implementation: `jvagent/action/email_action/` with `MicrosoftOutlookMailAction`
  - Filter: OData `outlook_mail_filter` on Inbox

- SendGrid - API key authentication
  - Auth: `SENDGRID_API_KEY` environment variable
  - Implementation: `jvagent/action/email_action/`
  - Feature: Inbound Parse webhook for email reception

**Custom Authentication:**
- jvspatial Server auth - Built-in
  - Implementation: `jvagent/cli/server_config.py` via jvspatial
  - Enabled by default: Authentication required for endpoints
  - CORS: Configurable via `CORSConfig` from jvspatial

## Monitoring & Observability

**Error Tracking:**
- Not detected in core framework
- Actions may implement custom error handling
- HTTPStatusError handling in web search actions (`jvagent/action/web_search/brave/brave.py`)

**Logs:**
- Approach: Python standard `logging` module
- Default configuration: CLI setup in `jvagent/cli/commands.py`
- Log retention: App-level configuration
  - Attribute: `log_retention_days` on App node (default: 60 days)
  - Location: `jvagent/core/app.py`
- Logging endpoints: `jvagent/logging/endpoints.py`

## CI/CD & Deployment

**Hosting:**
- Standalone application: `jvagent` CLI command
- Docker: Dockerfile generation via `jvagent bundle /path/to/app_directory`
- ASGI server: Uvicorn (production deployment)
- Cloud platforms: AWS Lambda (with distributed locking support), Heroku, any ASGI host

**CI Pipeline:**
- Not detected in core - Implementation via GitHub Actions referenced in docs
- Pre-commit hooks: `black`, `isort`, `flake8`, `mypy` configured in `pyproject.toml`
- Tests: `pytest` via `pytest tests/`

**Container Support:**
- Docker image generation: `jvagent bundle` command
- Location: `jvagent/bundle/` module

## Environment Configuration

**Required env vars:**
- `OPENAI_API_KEY` - OpenAI API access (if using OpenAI models)
- `ANTHROPIC_API_KEY` - Anthropic API access (if using Claude)
- `GOOGLE_CLIENT_SECRETS_JSON` - Google OAuth credentials (if using Gmail/Drive/etc.)
- `MICROSOFT_CLIENT_ID` - Microsoft OAuth credentials (if using Outlook)
- `JVSPATIAL_DB_TYPE` - Database type: `json` (default), `sqlite`
- `JVSPATIAL_DB_PATH` - Path to database file

**Optional env vars:**
- `BRAVE_SEARCH_API_KEY` - Brave Search integration
- `SERPAPI_API_KEY` - SerpAPI integration
- `SERPER_API_KEY` - Serper integration
- `ELEVENLABS_API_KEY` - ElevenLabs TTS
- `DEEPGRAM_API_KEY` - Deepgram STT
- `HEYGEN_API_KEY` - HeyGen video generation
- `FACEBOOK_ACCESS_TOKEN` - Facebook Graph API
- `POSTIZ_API_KEY` - Postiz social media
- `SENDGRID_API_KEY` - SendGrid email
- `TYPESENSE_API_KEY` - Typesense vector DB
- `JVAGENT_CONVERSATION_LOCK_REDIS_URL` - Redis distributed locks
- `JVAGENT_CONVERSATION_LOCK_DYNAMODB_TABLE` - DynamoDB distributed locks
- `EMAIL_DEFAULT_SENDER` - Default email sender address

**Secrets location:**
- `.env` file (local development) - Loaded via `python-dotenv`
- Environment variables (production)
- Cloud secrets manager (via environment) - AWS Secrets Manager, Azure Key Vault, etc.
- No hardcoded secrets in codebase

## Webhooks & Callbacks

**Incoming Webhooks:**
- WhatsApp inbound - Message webhook
  - Endpoint: Configured in WhatsApp provider
  - Protocol: HTTP POST
  - Handler: `jvagent/action/whatsapp/` action

- Facebook Messenger - Webhook for messages
  - Endpoint: Configured in Facebook app settings
  - Handler: `jvagent/action/facebook_action/endpoints.py` with messenger_webhook_helpers
  - Webhook verification: Custom handlers in `jvagent/action/facebook_action/messenger_webhook_helpers.py`

- Outlook Mail - Webhook for email (optional)
  - Endpoint: Configured in Microsoft notification subscription
  - Handler: `jvagent/action/email_action/` with Microsoft Graph change notification

- SendGrid Inbound Parse - Email webhook
  - Endpoint: SendGrid Inbound Parse webhook URL
  - Handler: `jvagent/action/email_action/`
  - Protocol: HTTP POST with parsed email data

**Outgoing Callbacks:**
- Generic HTTP callbacks
  - Implementation: `jvagent/core/callback.py`
  - Client: httpx async HTTP client
  - Timeout: 10 seconds default
  - Used for: Custom integrations, event notifications

## Integration Documentation

**Architecture References:**
- Core platform: `docs/agent-interact.md` - Agent interaction subsystem
- Language models: `docs/language-models.md` - LM provider configuration and retries

**Action Directory Structure:**
- All actions follow pattern: `jvagent/action/{namespace}/{action_name}/`
- Metadata: `info.yaml` with package info, dependencies, and configuration
- Implementation: `{action_name}.py` with Action subclass
- Endpoints: `endpoints.py` with FastAPI @endpoint decorators
- Init: `__init__.py` exports Action class and imports endpoints

## Model HTTP Retries Configuration

**BaseModelAction / LanguageModelAction:**
- All LM providers support configurable retries for transient errors
- Configuration fields:
  - `max_retries` - Maximum retry attempts
  - `retry_initial_delay` - Initial delay in seconds
  - `retry_max_delay` - Maximum delay cap
  - `retry_backoff_multiplier` - Exponential backoff factor
  - `retry_jitter` - Jitter to avoid thundering herd
  - `retry_on_status_codes` - HTTP status codes to retry on (timeouts, transport errors)
- Default values: Applied to all LM providers
- Override location: Per-action in `agent.yaml` context block
- Documentation: `docs/language-models.md`

---

*Integration audit: 2026-05-06*
