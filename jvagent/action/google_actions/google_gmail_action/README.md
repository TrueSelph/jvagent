# Google Gmail Action

Exposes Google Gmail operations (send, list, read) via the Google Gmail API using **OAuth 2.0** authentication.

## Requirements

- **Google Cloud project** with Gmail API enabled
- **OAuth 2.0 Client ID** configured (Web application or Desktop app)
- **Client Secrets JSON** downloaded from Google Cloud Console

## Configuration

| Attribute             | Description                                                         | Required |
| --------------------- | ------------------------------------------------------------------- | -------- |
| `client_secrets_json` | OAuth2 Client Secrets JSON (string or object)                       | Yes      |
| `redirect_uri`        | Redirect URI for OAuth2 flow (default: `urn:ietf:wg:oauth:2.0:oob`) | No       |

## Agent wiring (agent.yaml)

```yaml
- action: jvagent/google_actions/google_gmail_action
  context:
    client_secrets_json: ${GOOGLE_CLIENT_SECRETS_JSON}
```

Set `GOOGLE_CLIENT_SECRETS_JSON` in `.env` as a JSON string containing the downloaded credentials.

## Endpoints

| Method | Path                                        | Description                            |
| ------ | ------------------------------------------- | -------------------------------------- |
| GET    | `/agents/{agent_id}/google_gmail/auth_url`  | Get the OAuth2 authorization URL       |
| POST   | `/agents/{agent_id}/google_gmail/authorize` | Exchange authorization code for tokens |
| POST   | `/agents/{agent_id}/google_gmail/send`      | Send an email                          |
| GET    | `/agents/{agent_id}/google_gmail/list`      | List messages in inbox                 |
| GET    | `/agents/{agent_id}/google_gmail/read`      | Read a specific message                |

### Authorization Flow

1. Call `GET /agents/{agent_id}/google_gmail/auth_url` to receive the authorization URL.
2. Direct the user to the URL to grant permissions.
3. The user will receive an authorization code.
4. Call `POST /agents/{agent_id}/google_gmail/authorize` with the code to complete setup:

```json
POST /agents/{agent_id}/google_gmail/authorize
{
  "to": "recipient@example.com",
  "subject": "Hello from JV Agent",
  "body": "This is a test email sent via the Gmail API."
}
```

### List Messages

```http
GET /agents/{agent_id}/google_gmail/list?max_results=10&query=is:unread
```

### Read Message

```http
GET /agents/{agent_id}/google_gmail/read?message_id=1abc2def3ghi4jkl
```
