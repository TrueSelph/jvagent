---
name: google_calendar
description: >-
  List, create, and delete Google Calendar events. Use when the user asks
  about upcoming events, to schedule something, or to cancel a calendar event.
requires-actions:
  - GoogleCalendarAction
  - MCPAction
  - MCPOAuthAction
allowed-tools:
  - calendar__list_events
  - calendar__create_event
  - calendar__delete_event
---

# Google Calendar

Use these tools for Calendar. Pass **only** the parameters listed for each
tool. Omit unused optionals. Do not invent argument names. Times are ISO 8601.

Confirm before `calendar__delete_event`.

## Auth

If a tool fails because credentials are missing or expired, tell the operator
to open `/api/mcp/google_workspace/auth?service=calendar`. Do not send users to
`/api/google/...`. Do not ask the visitor to paste tokens.

## Tool parameters

### calendar__list_events

- `calendar_id` (optional) — default `primary`
- `time_min` (optional) — lower bound for event start (ISO 8601)
- `max_results` (optional) — default 10

### calendar__create_event

- `summary` (required) — event title
- `start_time` (required) — ISO 8601
- `end_time` (required) — ISO 8601
- `calendar_id` (optional) — default `primary`
- `description` (optional)
- `location` (optional)

### calendar__delete_event

- `calendar_id` (required) — pass `primary` if the user did not name a calendar
- `event_id` (required)
