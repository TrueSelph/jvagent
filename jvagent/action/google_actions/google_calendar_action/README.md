# Google Calendar Action

Exposes Google Calendar operations (list, create, delete) via the Google Calendar API using **OAuth 2.0** authentication.

## Requirements

- **Google Cloud project** with Calendar API enabled
- **OAuth 2.0 Client ID** configured (Web application or Desktop app)
- **Client Secrets JSON** downloaded from Google Cloud Console

## Configuration

| Attribute             | Description                                                         | Required |
| --------------------- | ------------------------------------------------------------------- | -------- |
| `client_secrets_json` | OAuth2 Client Secrets JSON (string or object)                       | Yes      |
| `redirect_uri`        | Redirect URI for OAuth2 flow (default: `urn:ietf:wg:oauth:2.0:oob`) | No       |
| `default_calendar_id` | Default calendar ID to operate on (e.g., 'primary')                 | No       |

## Agent wiring (agent.yaml)

```yaml
- action: jvagent/google_actions/google_calendar_action
  context:
    client_secrets_json: ${GOOGLE_CLIENT_SECRETS_JSON}
    default_calendar_id: "primary" # Optional: Defaults to 'primary'
```

Set `GOOGLE_CLIENT_SECRETS_JSON` in `.env` as a JSON string containing the downloaded credentials.

## Endpoints

| Method | Path                                           | Description                            |
| ------ | ---------------------------------------------- | -------------------------------------- |
| GET    | `/agents/{agent_id}/google_calendar/auth_url`  | Get the OAuth2 authorization URL       |
| POST   | `/agents/{agent_id}/google_calendar/authorize` | Exchange authorization code for tokens |
| GET    | `/agents/{agent_id}/google_calendar/list`      | List upcoming events                   |
| POST   | `/agents/{agent_id}/google_calendar/create`    | Create a new event                     |
| DELETE | `/agents/{agent_id}/google_calendar/delete`    | Delete an existing event               |

### Authorization Flow

1. Call `GET /agents/{agent_id}/google_calendar/auth_url` to receive the authorization URL.
2. Direct the user to the URL to grant permissions.
3. The user will receive an authorization code.
4. Call `POST /agents/{agent_id}/google_calendar/authorize` with the code to complete setup:

```json
POST /agents/{agent_id}/google_calendar/authorize
{
  "code": "4/0A..."
}
```

_Note: Tokens are automatically cached securely using native action file storage (`token.json`). If tokens expire, they will be refreshed automatically if a refresh token was granted._

### List Events

```http
GET /agents/{agent_id}/google_calendar/list?calendar_id=primary&max_results=10
{{ ... }}
```

Omit `calendar_id` to use `default_calendar_id` or 'primary'.

### Create Event

```json
POST /agents/{agent_id}/google_calendar/create
{
  "calendar_id": "primary",
  "summary": "Team Meeting",
  "description": "Weekly sync",
  "start_time": "2026-03-10T10:00:00Z",
  "end_time": "2026-03-10T11:00:00Z"
}
```

### Delete Event

```json
DELETE /agents/{agent_id}/google_calendar/delete
{
  "calendar_id": "primary",
  "event_id": "abc123xyz"
}
```
