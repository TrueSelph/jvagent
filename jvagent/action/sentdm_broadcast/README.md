# SentDM Broadcast Action

`SentDMBroadcastAction` wraps SentDM's [v3 REST API](https://docs.sent.dm/reference/api)
to send template-based SMS / WhatsApp broadcasts, read message status, list
templates, and receive delivery-status callbacks via an auto-registered webhook.

## Required environment variables

| Variable | Required | Purpose |
| --- | --- | --- |
| `SENTDM_API_KEY` | yes | Authenticates every call (`x-api-key` header). Create one in the SentDM dashboard. |
| `JVAGENT_PUBLIC_BASE_URL` | for webhooks | Public origin used to build the callback URL given to SentDM. |
| `JVSPATIAL_JWT_SECRET_KEY` | for webhooks | Required by jvspatial to create the system user that owns the webhook API key. |

The send endpoint requires a **pre-existing template** in your SentDM account.
Create templates from the SentDM dashboard (or `POST /v3/templates`) before
calling `send_broadcast`.

## Configuration

```yaml
actions:
  - action: jvagent/sentdm_broadcast_action
    context:
      enabled: true
      default_channels: ["sms", "whatsapp"]
      default_template_name: "order_confirmation"
      sandbox: false
```

### Attributes

| Attribute | Type | Default | Description |
| --- | --- | --- | --- |
| `api_base` | `str` | `"https://api.sent.dm"` | Base URL for the SentDM v3 API. |
| `default_channels` | `List[str]` | `["sms"]` | Channels used when a call omits `channels`. Values: `sms`, `whatsapp`, `rcs`. |
| `default_template_id` | `str` | `""` | Fallback template UUID for `send_broadcast`. |
| `default_template_name` | `str` | `""` | Fallback template name for `send_broadcast`. |
| `profile_id` | `str` | `""` | Optional `x-profile-id` value (org API keys scoping to a child profile). |
| `timeout` | `int` | `30` | HTTP timeout in seconds. |
| `sandbox` | `bool` | `false` | Default `sandbox` flag for mutating calls. |
| `webhook_display_name` | `str` | `"jvagent SentDM"` | Display name used when (re)creating the SentDM webhook. |
| `webhook_event_types` | `List[str]` | `["messages"]` | Event categories to subscribe to. Valid values: `messages`, `templates`. |
| `webhook_retry_count` | `int` | `3` | SentDM webhook retry count. |
| `webhook_timeout_seconds` | `int` | `30` | SentDM webhook delivery timeout. |

## Usage from another action

```python
sentdm = await self.get_action("SentDMBroadcastAction")
result = await sentdm.send_broadcast(
    to=["+14155551234", "+14155555678"],
    template={
        "name": "order_confirmation",
        "parameters": {"name": "Jane", "order_id": "12345"},
    },
    channels=["sms", "whatsapp"],
)
message_ids = [m.get("id") for m in (result.get("messages") or [])]
```

The response is the raw SentDM JSON. Inspect the API reference for the
per-recipient/per-channel message metadata.

## HTTP endpoints

All admin endpoints require auth (admin role) and are scoped by the action id:

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/api/actions/{action_id}/sentdm/broadcast` | Send a broadcast. Body: `{to, template?, channels?, parameters?, sandbox?, idempotency_key?, profile_id?}`. |
| `GET` | `/api/actions/{action_id}/sentdm/messages/{message_id}` | Get current status. |
| `GET` | `/api/actions/{action_id}/sentdm/messages/{message_id}/activities` | Get delivery activity log. |
| `GET` | `/api/actions/{action_id}/sentdm/templates` | List templates (forwards `page`, `page_size`, `search`, `status`, `category`). |
| `GET` | `/api/actions/{action_id}/sentdm/status` | Healthcheck (`/v3/me`). |
| `POST` | `/api/actions/{action_id}/sentdm/webhook/register` | Force webhook reconcile. |

Public webhook receiver (api_key auth via query/header — registered with SentDM
automatically on startup):

| Method | Path |
| --- | --- |
| `POST` | `/api/sentdm/webhook/{action_id}?api_key=...` |

The handler verifies the `X-Webhook-Signature` HMAC against the stored signing
secret and de-duplicates by `X-Webhook-ID`. Events are logged; downstream
dispatch can be added later.

## Interactive test CLI

A standalone, dependency-light CLI for exercising the endpoints lives at
[test_cli.py](test_cli.py). It does not import any jvagent code — it just
talks to a running jvagent server over HTTP.

```bash
python jvagent/action/sentdm_broadcast/test_cli.py
python jvagent/action/sentdm_broadcast/test_cli.py --env-file path/to/.env
python jvagent/action/sentdm_broadcast/test_cli.py --no-env   # ignore .env files
python jvagent/action/sentdm_broadcast/test_cli.py --prompt   # force interactive auth
python jvagent/action/sentdm_broadcast/test_cli.py -y         # explicit auto-mode
```

By default the CLI runs in **auto-mode** whenever the resolved env supplies
enough to authenticate (`JVAGENT_API_KEY`, or `JVAGENT_ADMIN_EMAIL` +
`JVAGENT_ADMIN_PASSWORD`). In auto-mode it skips every prompt that has a
default, auto-selects the agent that has a `SentDMBroadcastAction`, and drops
straight into the menu. Pass `--prompt` to force the legacy interactive flow.

What it does:

1. Loads a `.env` file for prompt defaults. By default it uses
   `examples/jvagent_app/.env` in this repository (the same file as the example
   app). If that path does not exist, it falls back to walking upward from the
   current working directory. Override with `--env-file`.
2. Prompts for the jvagent base URL — the default is
   `JVAGENT_BASE_URL` / `JVAGENT_PUBLIC_BASE_URL`, otherwise
   `http://{JVAGENT_HOST or localhost}:{JVAGENT_PORT or 8000}`.
3. Authenticates with either admin credentials (`POST /api/auth/login`) or an
   existing jvagent API key (`x-api-key` header).
4. Lists agents, lets you pick one, and locates its `SentDMBroadcastAction`.
5. Opens a menu: healthcheck, send broadcast (sandbox-on by default),
   list templates, get message status, get message activities, reconcile
   webhook, switch agent, quit.

### Env vars used as defaults

| Variable | Used for |
| --- | --- |
| `JVAGENT_BASE_URL` *(optional)* | Explicit base URL for the CLI |
| `JVAGENT_PUBLIC_BASE_URL` | Fallback base URL |
| `JVAGENT_HOST`, `JVAGENT_PORT` | Built into `http://host:port` as a last resort |
| `JVAGENT_ADMIN_EMAIL` | Default email for `POST /api/auth/login` (the jvspatial endpoint expects `email`, not `username`); falls back to `JVAGENT_ADMIN_USERNAME` |
| `JVAGENT_ADMIN_PASSWORD` | Skips the password prompt when present |
| `JVAGENT_API_KEY` | Skips the API-key prompt when present |
| `JVAGENT_API_KEY_HEADER` | Default header name (defaults to `x-api-key`) |

The CLI requires `httpx` (already a jvagent dependency) and uses
`python-dotenv` (also a jvagent dependency) for `.env` parsing. The last base
URL + chosen auth method are cached at `~/.sentdm_test_cli.json`. **Passwords
and API keys are never written to disk** — they're read fresh from `.env`
(or prompted) on each run.
