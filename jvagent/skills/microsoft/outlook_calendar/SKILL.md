---
name: outlook_calendar
description: >-
  List, create, and delete Outlook Calendar events. Use when the user asks
  about upcoming events, to schedule something, or to cancel a calendar event.
requires-actions:
  - MicrosoftOutlookCalendarAction
  - MCPAction
  - MCPOAuthAction
allowed-tools:
  - outlook_calendar__list_events
  - outlook_calendar__create_event
  - outlook_calendar__delete_event
---

# Outlook Calendar

Use these tools for Calendar. Pass **only** the parameters listed for each
tool. Omit unused optionals. Do not invent argument names. Times are ISO 8601.

Confirm before `outlook_calendar__delete_event`.

## Auth

If a tool fails because credentials are missing or expired, tell the operator
to open `/api/mcp/microsoft_365/auth?service=calendar`. Do not send users to
`/api/microsoft/...`. Do not ask the visitor to paste tokens.

## Tool parameters

### outlook_calendar__list_events

- `calendar_id` (optional) — default `primary`
- `time_min` (optional) — lower bound for event start (ISO 8601)
- `max_results` (optional) — default 10

### outlook_calendar__create_event

- `summary` (required) — event title
- `start_time` (required) — ISO 8601
- `end_time` (required) — ISO 8601
- `calendar_id` (optional) — default `primary`
- `description` (optional)
- `location` (optional)

### outlook_calendar__delete_event

- `calendar_id` (required) — pass `primary` if the user did not name a calendar
- `event_id` (required)
