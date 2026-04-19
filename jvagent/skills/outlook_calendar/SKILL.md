---
name: outlook_calendar
description: Manage Outlook Calendar events (list, create, delete).
requires-actions:
  - MicrosoftOutlookCalendarAction
allowed-tools:
  - outlook_calendar__list_events
  - outlook_calendar__create_event
  - outlook_calendar__delete_event
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

## Scope

This skill is for Outlook Calendar event workflows (list, create, delete). Use it for scheduling and event management in Microsoft calendars. Do not use it for email handling, file management, or document research.

## Grounding

- Only report event fields that were returned by the calendar tools (subject, time, attendees, IDs).
- If no events are found, explicitly state that no matching events were returned.
- Always confirm before `outlook_calendar__delete_event`, and reiterate the target event details.