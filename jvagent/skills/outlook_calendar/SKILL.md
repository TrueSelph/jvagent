---
name: outlook_calendar
description: Manage Outlook Calendar events (list, create, delete).
requires-actions:
  - MicrosoftOutlookCalendarAction
allowed-tools:
  - list_events
  - create_event
  - delete_event
version: 1
tags:
  - calendar
  - microsoft
---

## Workflow

1. Determine the calendar operation the user needs (list, create, or delete).
2. Use the appropriate Outlook Calendar tool to perform the operation.
3. Format the results clearly for the user.

### Constraints

- Always confirm with the user before deleting events.
- For creating events, ensure start and end times are valid ISO 8601.
- Default to the primary calendar unless the user specifies otherwise.