# Email action (Gmail or SendGrid)

Send transactional email and receive inbound mail on channel `email`. The action wires `EmailAdapter` / `EmailFilter` on the agent `ResponseBus` so persona replies are delivered by the configured provider.

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

**API**: `POST /api/actions/{action_id}/email/send` with `to`, `subject`, and `htmlContent` / `html_content` and/or `textContent` / `text_content`.

**Agent reply**: `EmailAdapter` uses `email_inbound` metadata for `Re:` subject and threading headers when present.

## Webhook URL

Admin: `GET /api/actions/{action_id}/email/webhook-url` — returns the callback URL with `api_key` for `webhook:email`. Use it for **SendGrid Inbound Parse**, or to **trigger one Gmail inbox fetch** when `provider` is `gmail` (e.g. from an external scheduler).
