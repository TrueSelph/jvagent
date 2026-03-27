# SendGridAction

Core action for [SendGrid Mail Send API v3](https://docs.sendgrid.com/api-reference/mail-send/mail-send): send transactional or marketing mail with arbitrary recipients, multipart content (plain, HTML, templates), and attachments. The HTTP integration uses **httpx** and a Bearer API key (no SendGrid Python SDK required).

## Configuration

| Field | Description |
|--------|-------------|
| `api_key` | SendGrid API key (`Authorization: Bearer …`) |
| `api_base_url` | Defaults to `https://api.sendgrid.com/v3` |
| `timeout` | HTTP timeout in seconds (default 30) |
| `default_from_email` | Used when a request omits `from` / `mail.from` |
| `default_from_name` | Optional display name paired with default from |

### Environment variables

If a context field is empty, these may fill it at runtime:

| Variable | Maps to |
|----------|---------|
| `SENDGRID_API_KEY` | `api_key` |
| `SENDGRID_API_BASE_URL` | `api_base_url` (only if still default) |
| `SENDGRID_FROM_EMAIL` | `default_from_email` |
| `SENDGRID_FROM_NAME` | `default_from_name` |

See also the SendGrid block in the jvagent repository [`.env.example`](../../../.env.example).

## Enable in an agent

Add to `agent.yaml`:

```yaml
actions:
  - action: jvagent/sendgrid_action
    context:
      enabled: true
      description: "SendGrid transactional email"
      api_key: "${SENDGRID_API_KEY}"
      default_from_email: "notifications@your-domain.com"
      default_from_name: "Your App"
```

After deploy, note the **action node id** (used as `{action_id}` in routes below). You can enable the action without `default_from_email` if every send includes a verified `from` in the request body.

## HTTP API (admin)

All routes require **admin** authentication.

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/actions/{action_id}/sendgrid/health` | Calls SendGrid `GET /user/profile` to validate the API key |
| POST | `/actions/{action_id}/sendgrid/send` | Send one message (convenience JSON or full v3 `mail` body) |

Successful sends usually return **HTTP 202** from SendGrid with an empty body. The action surfaces `status_code` and the `X-Message-Id` header (when present) under `result` in the JSON response.

### POST `/send` — two modes

**1. Full v3 payload** — pass a `mail` object exactly as SendGrid documents it (`personalizations`, `from`, `content`, `attachments`, `template_id`, etc.). Optional `mail_overrides` is deep-merged on top (useful to tweak `personalizations[0]` without rebuilding the whole body).

```json
{
  "mail": {
    "personalizations": [{ "to": [{ "email": "user@example.com", "name": "User" }] }],
    "from": { "email": "verified@your-domain.com", "name": "App" },
    "subject": "Hello",
    "content": [
      { "type": "text/plain", "value": "Plain text" },
      { "type": "text/html", "value": "<p>HTML</p>" }
    ]
  }
}
```

**2. Convenience fields** — omit `mail` and supply at least `to`. The action builds a v3 body for you.

Supported body keys include:

| Key | Notes |
|-----|--------|
| `to` | **Required** (unless using `mail`). String, list of emails, or list of `{ "email", "name?" }` |
| `cc`, `bcc` | Same shapes as `to` |
| `from` | Object `{ "email", "name?" }` (or rely on `default_from_email`) |
| `subject`, `text`, `html` | Subject and body parts; for **dynamic templates** you may omit `text`/`html` and set `template_id` instead |
| `reply_to` | Email string or `{ "email", "name?" }` |
| `headers` | String map of custom headers |
| `categories` | String array |
| `template_id` | Dynamic template id |
| `dynamic_template_data` | Object merged into the first personalization |
| `attachments` | List of objects (see below) |
| `mail_overrides` | Optional object merged into the constructed or raw payload |

**Attachments** (convenience mode) — each item should include base64 content and metadata SendGrid expects:

```json
{
  "to": "someone@example.com",
  "subject": "Report",
  "text": "See attached.",
  "attachments": [
    {
      "filename": "report.pdf",
      "content_base64": "<base64>",
      "type": "application/pdf",
      "disposition": "attachment"
    }
  ]
}
```

You may use `content` instead of `content_base64`. Optional `content_id` is supported for inline images when combined with appropriate `disposition` / HTML.

SendGrid enforces a **total message size** on the order of tens of megabytes (including all attachments); very large payloads will fail at the API.

## Code usage

```python
sg = await agent.get_action_by_type("SendGridAction")

# Full v3 control
await sg.send_mail_v3({
    "personalizations": [{"to": [{"email": "a@b.com"}]}],
    "from": {"email": "verified@your-domain.com"},
    "subject": "Hi",
    "content": [{"type": "text/plain", "value": "Hello"}],
})

# Convenience helper
await sg.send_mail(
    to=["a@b.com", {"email": "c@d.com", "name": "Pat"}],
    subject="Hi",
    html="<p>Hello</p>",
    mail_overrides={"headers": {"X-Custom": "value"}},
)
```

Persona integration: when this action is enabled, it contributes a short capability line via `get_capabilities()` so the persona can describe email-sending ability to the model.

## References

- [Mail Send API](https://docs.sendgrid.com/api-reference/mail-send/mail-send)
- [How to create and send email with dynamic templates](https://docs.sendgrid.com/ui/sending-email/how-to-send-an-email-with-dynamic-templates)
