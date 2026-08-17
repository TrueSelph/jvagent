---
name: google_gmail
description: >-
  Send and read Gmail. Use when the user asks to send mail, list inbox
  messages, open a message, mark mail read, or check the mailbox profile.
requires-actions:
  - GoogleGmailAction
  - MCPAction
  - MCPOAuthAction
allowed-tools:
  - gmail__send_email
  - gmail__list_messages
  - gmail__get_message
  - gmail__mark_read
  - gmail__get_profile
---

# Gmail

Use these tools for the authenticated Gmail mailbox. Pass **only** the
parameters listed for each tool. Omit unused optionals. Do not invent
argument names.

## Auth

If a tool fails because credentials are missing or expired, tell the operator
to open `/api/mcp/google_workspace/auth?service=gmail`. Do not send users to
`/api/google/...`. Do not ask the visitor to paste tokens.

## Tool parameters

### gmail__send_email

- `to` (required) — recipient email
- `subject` (required)
- `body` (optional) — HTML body

### gmail__list_messages

- `query` (optional) — Gmail search query
- `max_results` (optional) — default 10

### gmail__get_message

- `message_id` (required)
- `fmt` (optional) — message format, default `full`

### gmail__mark_read

- `message_id` (required)

### gmail__get_profile

No parameters.
