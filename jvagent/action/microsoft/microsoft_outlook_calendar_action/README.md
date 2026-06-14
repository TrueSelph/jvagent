# Microsoft Outlook Calendar Action

List, create, and delete calendar events via **Microsoft Graph**, with **Entra ID** OAuth (PKCE) on `MicrosoftAction`.

Shared setup is in the [Microsoft actions README](../README.md).

## Microsoft Graph scopes

Delegated scopes:

- `offline_access`
- `User.Read`
- `Calendars.ReadWrite`

## Agent wiring (`agent.yaml`)

```yaml
- action: jvagent/microsoft_outlook_calendar_action
```

Complete OAuth via each instance’s **`auth_url`** after `MICROSOFT_CLIENT_ID` (and related env) are set.

## REST API (unified calendar)

Paths assume the default `/api` prefix. Admin-authenticated routes; see OpenAPI for parameters.

| Method | Path | Description |
| ------ | ---- | ----------- |
| GET | `/api/microsoft/{action_id}` | OAuth landing page |
| GET | `/api/actions/{action_id}/list` | List events: `calendar_id` (`primary` or calendar id), `time_min` (OData filter on `start/dateTime`), `max_results` |
| POST | `/api/actions/{action_id}/create` | Create event: `summary`, `start_time`, `end_time`, optional `calendar_id`, `description`, `location` |
| DELETE | `/api/actions/{action_id}/delete` | `calendar_id`, `event_id` |

## Behavior notes

- **`calendar_id`**: `primary` (default) uses `/me/events`; a specific id uses `/me/calendars/{id}/events`.
- **Times**: `create_event` sends `start` / `end` with `timeZone: "UTC"`; pass ISO-style `time` strings Graph accepts.
- **List shape**: Normalized fields include `id`, `summary` (subject), `start`, `end`, `location`, `description`, `webLink`.
