---
name: calendar
description: Manage Google Calendar events (list, create, delete).
requires-actions:
  - GoogleCalendarAction
allowed-tools:
  - calendar__list_events
  - calendar__create_event
  - calendar__delete_event
version: 1
tags:
  - calendar
  - google
---

## Workflow

1. Determine the calendar operation the user needs (list, create, or delete).
2. Use the appropriate calendar tool to perform the operation.
3. Format the results clearly for the user.

### Constraints

- Always confirm with the user before deleting events.
- For creating events, ensure start and end times are valid ISO 8601.
- Default to the primary calendar unless the user specifies otherwise.

## Scope

This skill is for Google Calendar event operations (list, create, delete). Use it when the request is specifically about calendar scheduling or event management. Do not use it for email, document, or unrelated research tasks.

## Grounding

- Only report event details (title, time, attendees, calendar) that were actually returned by calendar tools.
- When event queries return no matches, explicitly say no events were found rather than inventing plausible entries.
- Always confirm before destructive changes such as `calendar__delete_event`, and restate what will be deleted.