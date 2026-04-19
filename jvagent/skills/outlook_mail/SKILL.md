---
name: outlook_mail
description: Send and manage Outlook mail messages.
requires-actions:
  - MicrosoftOutlookMailAction
allowed-tools:
  - outlook_mail__send_email
  - outlook_mail__list_messages
  - outlook_mail__list_inbox_messages
  - outlook_mail__get_message
  - outlook_mail__mark_read
  - outlook_mail__get_profile
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
- For `outlook_mail__send_email`, the `data` parameter is an object with `to`, `subject`, and `body` fields.
- Default to the authenticated user ("me") unless the user specifies otherwise.
- `outlook_mail__list_inbox_messages` filters by OData expression; use `outlook_mail__list_messages` for Gmail-style query syntax.

## Scope

This skill is for Outlook mail operations: list/read messages, mark as read, profile lookup, and send. Use it for Microsoft mailbox workflows. Do not use it for calendar, OneDrive, or spreadsheet requests.

## Grounding

- Only report message attributes actually returned by tools; do not fabricate sender names, subjects, timestamps, or bodies.
- If search/filtering yields no messages, state that explicitly.
- Always confirm before `outlook_mail__send_email`, including recipient and subject.