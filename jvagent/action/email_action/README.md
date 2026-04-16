# Email action (Gmail or SendGrid)

Send transactional email and receive inbound mail on channel `email`. The action wires `EmailAdapter` / `EmailFilter` on the agent `ResponseBus`. By default **`deliver_response_bus_adhoc`** is **false**, so **adhoc** bus messages do not trigger outbound mail (avoids one email per chunk). Use **`POST .../email/send`** or **`provider.send_canonical`** for outbound mail, or set **`deliver_response_bus_adhoc`** to **true** on **EmailAction** if you want persona publishes delivered as email.

## Providers

- **`gmail` (default)** — Outbound via Gmail API (OAuth on **`GoogleGmailAction`** on the same agent). Inbound via the **email webhook**: each POST with `api_key` runs **one** inbox fetch (`users.messages.list` / `get` with `format=raw`). The first message matching `gmail_list_query` that passes access control is marked read, then processed. The same run is available for admins as `POST /api/actions/{action_id}/email/gmail/fetch-inbox-once`. The POST body is ignored for Gmail. Inbound **interaction utterance** is the email **subject** (or `(no subject)`); **plain/HTML body** is stored on `email_inbound.BodyPlain` / `BodyHtml`, and small inline **images** are added to `image_urls` for vision (same idea as WhatsApp). Persona appends the body to the model prompt when `channel=email`.
- **`sendgrid`** — Outbound via Mail Send v3; inbound via **SendGrid Inbound Parse** posting to `/api/email/interact/webhook/{agent_id}`.

## Environment variables

| Variable | Required | Purpose |
|----------|----------|---------|
| `GOOGLE_CLIENT_SECRETS_JSON` | Gmail | Path or JSON string (same as other Google actions) |
| `EMAIL_DEFAULT_SENDER` | SendGrid send; optional Gmail | Default `From` (Gmail can use mailbox profile if unset) |
| `EMAIL_DEFAULT_SENDER_NAME` | No | Display name for the default sender |
| `SENDGRID_API_KEY` | SendGrid | REST API key |
| `JVAGENT_PUBLIC_BASE_URL` | Webhook URL generation | Public HTTPS base for inbound webhook URLs (Gmail and SendGrid) |

## Send flow

**API**: `POST /api/actions/{action_id}/email/send` with `to`, `subject`, and `htmlContent` / `html_content` and/or `textContent` / `text_content`. Optional **`cc`** / **`ccRecipients`**: list of email strings or `{ "email", "name" }` objects (same shape across Gmail, Outlook, SendGrid).

**Agent reply (when you send via adapter yourself)**: `EmailAdapter` uses `email_inbound` for `Re:` subject, **`In-Reply-To` / `References`** when **`MessageId`** (or optional inbound **`InReplyTo`**) is present, and **`Cc`** on the outbound message when the inbound message included CC. You can also set **`parent_message_id`** or **`email_parent_message_id`** on message metadata to thread to a specific message id (overrides **`email_inbound.MessageId`** for those headers).

## Webhook URL

Admin: `GET /api/actions/{action_id}/email/webhook-url` — returns the callback URL with `api_key` for `webhook:email`. Use it for **SendGrid Inbound Parse**, or to **trigger one Gmail inbox fetch** when `provider` is `gmail` (e.g. from an external scheduler).

```
curl -X 'POST' \
  'webhook_url' \
  -H 'accept: application/json' \
  -d ''
```