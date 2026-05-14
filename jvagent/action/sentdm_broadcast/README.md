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
| `webhook_event_types` | `List[str]` | `["message"]` | Parent categories for Sent ``POST /v3/webhooks`` (``message``, ``templates``). Sub-types use ``webhook_event_filters``. |
| `webhook_event_filters` | `Dict[str, List[str]] \| null` | `null` → default ``{message: [queued, sent, delivered, read, failed, received]}`` | Sent ``event_filters``; ``{}`` means all sub-types for subscribed parents. |
| `webhook_retry_count` | `int` | `3` | SentDM webhook retry count. |
| `webhook_timeout_seconds` | `int` | `30` | SentDM webhook delivery timeout. |
| `persist_records` | `bool` | `true` | Persist a `SentDMBroadcastRecord` per `(recipient, channel)` on each send so webhook events update the graph. |
| `persist_sandbox_sends` | `bool` | `false` | Persist sandbox sends as well (off by default to keep the graph free of test traffic). |
| `record_event_history_limit` | `int` | `25` | Bound on the `events[]` audit log per record (FIFO eviction). |

## Graph data model

When `persist_records` is on, every successful `POST /v3/messages` writes one
`SentDMBroadcastRecord` node per `(message_id, recipient, channel)` and
connects it to the action node, so the graph layout is:

```
Agent -> Actions -> SentDMBroadcastAction -> SentDMBroadcastRecord (one per message)
```

Indexed fields (cheap lookups):

| Field | Purpose |
| --- | --- |
| `action_id` | Filter records belonging to a specific action node. |
| `agent_id` | Cross-action queries scoped to one agent. |
| `sentdm_message_id` | Primary key used by the webhook handler to reconcile delivery status into the local record. |

Mutable fields:

| Field | Notes |
| --- | --- |
| `status` | Normalized: `accepted` / `processing` / `queued` / `sent` / `delivered` / `read` / `received` / `failed` / `rejected` / `undelivered`. |
| `last_event_field`, `last_event_payload`, `last_status_at` | Snapshot of the most recent webhook (or refresh) event. |
| `events` | Bounded audit log (newest last); cap from `record_event_history_limit`. |
| `error` | Populated when `status in {failed, rejected, undelivered}`. |
| `to`, `channel`, `template_id`, `template_name`, `parameters`, `idempotency_key`, `profile_id`, `sandbox`, `created_at`, `updated_at` | Captured at send time. |

Status derivation when a webhook arrives:

1. If `payload.status` or `payload.message_status` (or nested `data.*`) matches a known token, use it.
2. Else if `payload.event`, `eventType`, or `sub_type` looks like `message.delivered` / `message.sent` / `message.failed`, use the suffix after the dot.
3. Else keep the prior status; only `last_event_field` / `events` are updated.

If no local record exists for the inbound `sentdm_message_id` (e.g.
`persist_records=false`, or the send was made from a different node), the
handler still returns `200` so SentDM stops retrying and logs an `info` line.

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
| `POST` | `/api/actions/{action_id}/broadcast` | Send a broadcast. Body: `{to, template?, channels?, parameters?, sandbox?, idempotency_key?, profile_id?}` (see OpenAPI / docstring example). |
| `POST` | `/api/actions/{action_id}/webhook/register` | Force webhook reconcile (re-creates SentDM webhook to point at us). |
| `GET` | `/api/actions/{action_id}/webhook` | Read-only view of the currently registered webhook URL + signing-secret status. |

Public webhook receiver (api_key auth via query/header — registered with SentDM
automatically on startup):

| Method | Path |
| --- | --- |
| `POST` | `/api/webhook/{action_id}?api_key=...` |

The handler verifies the `X-Webhook-Signature` HMAC against the stored signing
secret, de-duplicates by `X-Webhook-ID`, looks up the matching
`SentDMBroadcastRecord` (by `sentdm_message_id` parsed out of the event
payload), and folds the event into the record's `status`, `last_status_at`,
`last_event_payload` and `events[]` audit log.

Webhook **reconcile** (on action load or `POST …/webhook/register`) deletes every
other Sent webhook whose URL starts with
`{JVAGENT_PUBLIC_BASE_URL}/api/webhook/` except the exact URL for **this**
action — including other action ids on the same host. If you run multiple
SentDM broadcast actions behind one public base URL, reconciling one action
removes the others' Sent registrations; use distinct base URLs (or hostnames)
per deployment if you need them concurrently.

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
5. Opens a menu: send broadcast (sandbox-on by default), reconcile webhook,
   show registered webhook URL, switch agent, quit.

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
