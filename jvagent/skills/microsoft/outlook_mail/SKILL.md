---
name: outlook_mail
description: >-
  Send and read Outlook mail. Use when the user asks to send mail, list inbox
  messages, open a message, mark mail read, or check the mailbox profile.
requires-actions:
  - MicrosoftOutlookMailAction
  - MCPAction
  - MCPOAuthAction
allowed-tools:
  - outlook__send_email
  - outlook__list_messages
  - outlook__list_inbox_messages
  - outlook__get_message
  - outlook__mark_read
  - outlook__get_profile
---

# Outlook Mail

Use these tools for the authenticated Outlook mailbox. Pass **only** the
parameters listed for each tool. Omit unused optionals. Do not invent
argument names.

## Auth

If a tool fails because credentials are missing or expired, tell the operator
to open `/api/mcp/microsoft_365/auth?service=outlook`. Do not send users to
`/api/microsoft/...`. Do not ask the visitor to paste tokens.

## Tool parameters

### outlook__send_email

- `to` (required) — recipient email
- `subject` (required)
- `body` (optional) — HTML body

### outlook__list_messages

- `query` (optional) — search query
- `max_results` (optional) — default 10
- `user_id` (optional) — default `me`

### outlook__list_inbox_messages

- `odata_filter` (optional) — default `isRead eq false`
- `max_results` (optional) — default 25
- `user_id` (optional) — default `me`

### outlook__get_message

- `message_id` (required)
- `user_id` (optional) — default `me`

### outlook__mark_read

- `message_id` (required)
- `user_id` (optional) — default `me`

### outlook__get_profile

- `user_id` (optional) — default `me`
