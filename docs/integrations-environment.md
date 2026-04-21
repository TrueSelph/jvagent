# Integration environment variables

Variables used by **actions** and **integrations** outside the `JVSPATIAL_*` / `JVAGENT_*` core matrices in [configuration.md](configuration.md).

For the complete consolidated key inventory, see [environment-keys-reference.md](environment-keys-reference.md).

## OpenAI and PageIndex tree search

| Variable | Role |
|----------|------|
| **`OPENAI_API_KEY`** | OpenAI-compatible APIs and PageIndex `tree_search`. |
| **`PAGEINDEX_TREE_SEARCH_MODEL`** | Model id for tree search (default `gpt-4o-mini`). |

## Anthropic (native Messages API)

| Variable | Role |
|----------|------|
| **`ANTHROPIC_API_KEY`** | Native Anthropic Messages API authentication for `jvagent/anthropic_lm`. |

If you are using Anthropic models through OpenRouter (`anthropic/...` model ids),
configure `OPENAI_API_KEY` as required by the OpenRouter action in this codebase.

Actions resolve secrets from environment only (env-first) and do not rely on
persisted DB secret values. Secret-like keys provided in `agent.yaml` context are
ignored with a warning when they do not exist on the action class.

## Ollama (native local runtime)

No API key is required for default local Ollama usage. Configure endpoint/model
on the action context (for example `api_endpoint: http://localhost:11434`).

| Variable | Role |
|----------|------|
| `OLLAMA_HOST` | Optional host override for the Ollama daemon process itself (used by Ollama runtime tooling, not read directly by jvagent actions). |

## Public app URL (jvagent)

| Variable | Role |
|----------|------|
| **`JVAGENT_PUBLIC_BASE_URL`** | Public origin for WhatsApp/Facebook/Google webhooks, OAuth redirects, and resolving relative media URLs. Required for PageIndex LLM webhook URL generation when using **jvforge** (delegated ingest). |

## jvforge (PageIndex)

When jvforge runs as a separate service, set **`JVAGENT_JVFORGE_BASE_URL`** to its origin (for example `http://127.0.0.1:8088`). REST ingest and Google Drive sync then POST documents to jvforge `POST /v1/process` with an `llm_webhook_url` pointing at jvagent. If jvforge is configured to require authentication, set **`JVAGENT_JVFORGE_API_KEY`** or **`JVFORGE_API_KEY`** (sent as `X-API-Key`). See `jvforge/README.md` in the jvforge package.

| Variable | Role |
|----------|------|
| **`JVAGENT_JVFORGE_BASE_URL`** | jvforge service origin; when set, PageIndex ingest is delegated to jvforge. |
| **`JVAGENT_JVFORGE_API_KEY`** / **`JVFORGE_API_KEY`** | Optional API key for jvforge inbound requests. |

## WhatsApp

| Variable | Role |
|----------|------|
| `WHATSAPP_API_URL` | Provider API base URL |
| `WHATSAPP_API_KEY` | API key / token |
| `WHATSAPP_SESSION` | Session id (providers that need it) |
| `WHATSAPP_TOKEN` | Alternate token (some providers) |
| `WHATSAPP_SESSION_REGISTER_TIMEOUT_SECONDS` | Session registration timeout |
| `WHATSAPP_SKIP_STARTUP_REGISTRATION` | Skip startup registration when `true` |
| `WHATSAPP_REQUEST_TIMEOUT` | HTTP timeout override |

## Facebook / Messenger

| Variable | Role |
|----------|------|
| `FACEBOOK_API_URL` | Custom Graph base (optional) |
| `FACEBOOK_APP_SECRET` | App secret |
| `FACEBOOK_APP_ID` | App id |
| `FACEBOOK_PAGE_ID` | Page id |
| `FACEBOOK_ACCESS_TOKEN` | User/system token |
| `FACEBOOK_VERIFY_TOKEN` | Webhook verify token |
| `FACEBOOK_WEBHOOK_FIELDS` | Subscribed fields |
| `FACEBOOK_PAGE_ACCESS_TOKEN` | Page access token |
| `FACEBOOK_GRAPH_BASE` | Graph host override |
| `FACEBOOK_GRAPH_VERSION` | API version (default `v25.0`) |
| `MESSENGER_MESSAGE_WINDOW` | Debounce window (seconds) for merging webhook payloads |
| `FACEBOOK_RELOAD_WEBHOOK_SUBSCRIBE` | Reload webhook subscribe behavior |
| `FACEBOOK_SKIP_STARTUP_WEBHOOK_REGISTRATION` | Defer Meta subscription |
| `FACEBOOK_WEBHOOK_REGISTER_DELAY_SECONDS` | Delay before deferred Meta subscribe (default `8`) |

## Email (`jvagent/email_action`, provider `gmail` or `sendgrid`)

| Variable | Role |
|----------|------|
| `GOOGLE_CLIENT_SECRETS_JSON` | Gmail (and Google OAuth actions): client secrets path or JSON |
| `EMAIL_DEFAULT_SENDER` | Default From; optional for Gmail if OAuth profile has an address; required for SendGrid send |
| `EMAIL_DEFAULT_SENDER_NAME` | Optional From display name |
| `SENDGRID_API_KEY` | SendGrid Mail Send v3 when `provider=sendgrid` |
| `SENDGRID_API_BASE_URL` | Optional API base override (default `https://api.sendgrid.com/v3`) |
| `SENDGRID_FROM_EMAIL` / `SENDGRID_FROM_NAME` | Optional fallback if `EMAIL_DEFAULT_SENDER` / name unset |

## Web search (Serper)

| Variable | Role |
|----------|------|
| **`SERPER_API_KEY`** | Serper API subscription token |

## Audio / TTS / STT (examples)

Common patterns in templates and actions (exact names depend on the action package):

| Variable | Role |
|----------|------|
| `TTS_API_KEY` | TTS provider |
| `STT_API_KEY` | STT provider |
| `ELEVENLABS_API_KEY` | ElevenLabs |
| `DEEPGRAM_API_KEY` | Deepgram |

## Vector / search (examples)

| Variable | Role |
|----------|------|
| `TYPESENSE_API_KEY` | Typesense (when used by an action) |

## AWS / runtime (often implicit)

| Variable | Role |
|----------|------|
| `AWS_LAMBDA_FUNCTION_NAME` | Detected in some actions for Lambda-specific behavior |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | DynamoDB and other AWS clients (see [configuration.md](configuration.md) database table) |

## File storage

Use **`JVSPATIAL_FILE_STORAGE_PROVIDER`** and related `JVSPATIAL_FILE_STORAGE_*` keys from [configuration.md](configuration.md).

This inventory is derived from action code and templates; when in doubt, grep `os.getenv` / `os.environ` under `jvagent/action/` (excluding vendored trees you do not own).
