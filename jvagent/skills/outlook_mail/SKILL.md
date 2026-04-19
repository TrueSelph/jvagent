---
name: outlook_mail
description: Send and manage Outlook mail messages.
requires-actions:
  - MicrosoftOutlookMailAction
allowed-tools:
  - send_email
  - list_messages
  - list_inbox_messages
  - get_message
  - mark_read
  - get_profile
version: 1
tags:
  - email
  - microsoft
---

## Workflow

1. Determine the Outlook Mail operation the user needs (send, list, read, mark read, or profile).
2. Use the appropriate Outlook Mail tool to perform the operation.
3. Format the results clearly for the user.

### Constraints

- Always confirm with the user before sending email.
- For `send_email`, the `data` parameter is an object with `to`, `subject`, and `body` fields.
- Default to the authenticated user ("me") unless the user specifies otherwise.
- `list_inbox_messages` filters by OData expression; use `list_messages` for Gmail-style query syntax.