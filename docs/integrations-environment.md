# Integration environment variables

Variables used by **actions** and **integrations** outside the `JVSPATIAL_*` / `JVAGENT_*` core matrices in [configuration.md](configuration.md).

## OpenAI and PageIndex tree search

| Variable | Role |
|----------|------|
| **`OPENAI_API_KEY`** | OpenAI-compatible APIs and PageIndex `tree_search`. |
| **`PAGEINDEX_TREE_SEARCH_MODEL`** | Model id for tree search (default `gpt-4o-mini`). |

## Public app URL (jvagent)

| Variable | Role |
|----------|------|
| **`JVAGENT_PUBLIC_BASE_URL`** | Public origin for WhatsApp/Facebook/Google webhooks, OAuth redirects, and resolving relative media URLs. |

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

## SendGrid

| Variable | Role |
|----------|------|
| `SENDGRID_API_KEY` | API key (`Authorization: Bearer …`) when `api_key` is unset in context |
| `SENDGRID_API_BASE_URL` | API base (default `https://api.sendgrid.com/v3`) when still default |
| `SENDGRID_FROM_EMAIL` | Default from address when `default_from_email` is unset |
| `SENDGRID_FROM_NAME` | Default from display name when `default_from_name` is unset |

## Web search (Serper)

Typically set via `app.yaml` placeholder (e.g. `api_key: ${SERPER_API_KEY}`):

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
