# Email action (Gmail, Outlook, or SendGrid)

Send transactional email and receive inbound mail on channel `email`. The action wires `EmailAdapter` / `EmailFilter` on the agent `ResponseBus`. By default **`deliver_response_bus_adhoc`** is **false**, so **adhoc** bus messages do not trigger outbound mail (avoids one email per chunk). Use **`POST .../email/send`** or **`provider.send_canonical`** for outbound mail, or set **`deliver_response_bus_adhoc`** to **true** on **EmailAction** if you want persona publishes delivered as email.

## Providers

- **`gmail` (default)** — Outbound via Gmail API on **`GoogleGmailAction`** (same agent). **Login is MCP OAuth** (`MCPOAuthToken`) at `/api/mcp/google_workspace/auth?service=gmail`, not `/api/google/...`. Inbound via the **email webhook**: each POST with `api_key` runs **one** inbox fetch (`users.messages.list` / `get` with `format=raw`). The first message matching `gmail_list_query` that passes access control is marked read, then processed. The same run is available for admins as `POST /api/actions/{action_id}/email/gmail/fetch-inbox-once`. The POST body is ignored for Gmail. Inbound **interaction utterance** is the email **subject** (or `(no subject)`); **plain/HTML body** is stored on `email_inbound.BodyPlain` / `BodyHtml`, and small inline **images** are added to `image_urls` for vision (same idea as WhatsApp). Persona appends the body to the model prompt when `channel=email`.
- **`outlook`** — Outbound via Microsoft Graph on **`MicrosoftOutlookMailAction`**. **Login is MCP OAuth** at `/api/mcp/microsoft_365/auth?service=outlook`, not `/api/microsoft/...`. Inbound webhook POST runs one Inbox fetch matching `outlook_mail_filter`.
- **`sendgrid`** — Outbound via Mail Send v3; inbound via **SendGrid Inbound Parse** posting to `/api/email/interact/webhook/{agent_id}`. Inbound messages are accepted only when SendGrid reports **SPF=pass** and **DKIM pass** (forged `From` is dropped). Gmail/Outlook poll paths trust the authenticated API mailbox, not SMTP headers.
- **`POST .../email/send`** requires **admin** role (same as other email admin endpoints).

## Environment variables

| Variable | Required | Purpose |
|----------|----------|---------|
| `GOOGLE_CLIENT_SECRETS_JSON` | Gmail | Path or JSON string (same as other Google actions; used to refresh MCP tokens) |
| `MICROSOFT_CLIENT_ID` | Outlook | Entra application (client) ID |
| `MICROSOFT_CLIENT_SECRET` | Outlook (confidential clients) | Entra client secret |
| `MICROSOFT_TENANT_ID` | No | Tenant id, or `common` (default) |
| `EMAIL_DEFAULT_SENDER` | SendGrid send; optional Gmail/Outlook | Default `From` (Gmail/Outlook can use mailbox profile if unset) |
| `EMAIL_DEFAULT_SENDER_NAME` | No | Display name for the default sender |
| `SENDGRID_API_KEY` | SendGrid | REST API key |
| `JVAGENT_PUBLIC_BASE_URL` | Webhook URL and MCP callback | Public HTTPS origin for inbound webhook URLs and MCP OAuth redirect |

## Agent wiring (`agent.yaml`)

Gmail:

```yaml
- action: jvagent/mcp_oauth
  context:
    enabled: true

- action: jvagent/mcp
  context:
    enabled: true
    servers:
      - name: google_workspace
        enabled: true
        transport: stdio
        command: npx
        args: ["-y", "@aaronsb/google-workspace-mcp"]
        tools: ["manage_email"]
        show_tools: false
        sandbox_mode: false

- action: jvagent/google_gmail_action
  context:
    enabled: true

- action: jvagent/email_action
  context:
    provider: gmail
    enabled: true
```

Authorize at **`/api/mcp/google_workspace/auth?service=gmail`**.

Outlook:

```yaml
- action: jvagent/mcp_oauth
  context:
    enabled: true

- action: jvagent/mcp
  context:
    enabled: true
    servers:
      - name: microsoft_365
        enabled: true
        transport: stdio
        command: npx
        args: ["-y", "@softeria/ms-365-mcp-server"]
        tools: "-all"
        sandbox_mode: false

- action: jvagent/microsoft_outlook_mail_action
  context:
    enabled: true

- action: jvagent/email_action
  context:
    provider: outlook
    enabled: true
```

Authorize at **`/api/mcp/microsoft_365/auth?service=outlook`**. Entra redirect URI: `{JVAGENT_PUBLIC_BASE_URL}/api/mcp/microsoft_365/auth/callback`.

## Send flow

**API**: `POST /api/actions/{action_id}/email/send` with `to`, `subject`, and `htmlContent` / `html_content` and/or `textContent` / `text_content`. Optional **`cc`** / **`ccRecipients`**: list of email strings or `{ "email", "name" }` objects (same shape across Gmail, Outlook, SendGrid).

**Agent reply (when you send via adapter yourself)**: `EmailAdapter` uses `email_inbound` for `Re:` subject, **`In-Reply-To` / `References`** when **`MessageId`** (or optional inbound **`InReplyTo`**) is present, and **`Cc`** on the outbound message when the inbound message included CC. You can also set **`parent_message_id`** or **`email_parent_message_id`** on message metadata to thread to a specific message id (overrides **`email_inbound.MessageId`** for those headers).

## Webhook URL

Admin: `GET /api/actions/{action_id}/email/webhook-url` — returns the callback URL with `api_key` for `webhook:email`. Use it for **SendGrid Inbound Parse**, or to **trigger one Gmail/Outlook inbox fetch** when `provider` is `gmail` or `outlook` (e.g. from an external scheduler).

```
curl -X 'POST' \
  'webhook_url' \
  -H 'accept: application/json' \
  -d ''
```
