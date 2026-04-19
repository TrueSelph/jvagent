---
name: gmail
description: Send and manage Gmail messages.
requires-actions:
  - GoogleGmailAction
allowed-tools:
  - send_email
  - list_messages
  - get_message
  - mark_read
  - get_profile
version: 1
tags:
  - email
  - google
---

## Workflow

1. Determine the Gmail operation the user needs (send, list, read, mark read, or profile).
2. Use the appropriate Gmail tool to perform the operation.
3. Format the results clearly for the user.

### Constraints

- Always confirm with the user before sending email.
- For `send_email`, the `data` parameter is an object with `to`, `subject`, and `body` fields.
- Default to the authenticated user ("me") unless the user specifies otherwise.