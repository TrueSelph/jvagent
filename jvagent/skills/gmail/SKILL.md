---
name: gmail
description: Send and manage Gmail messages.
requires-actions:
  - GoogleGmailAction
allowed-tools:
  - gmail__send_email
  - gmail__list_messages
  - gmail__get_message
  - gmail__mark_read
  - gmail__get_profile
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
- For `gmail__send_email`, the `data` parameter is an object with `to`, `subject`, and `body` fields.
- Default to the authenticated user ("me") unless the user specifies otherwise.

## Scope

This skill is for Gmail mailbox and sending workflows. Use it for listing, reading, marking, profile lookup, or sending Gmail messages. Do not use it for calendar operations, file management, or non-email research.

## Grounding

- Only report message metadata and body content that tools actually return; do not fabricate senders, subjects, snippets, or thread IDs.
- When searches return no messages, state that no matching email was found instead of inferring likely results.
- Always confirm recipient, subject, and intent before calling `gmail__send_email`.