# Google Sheets Action

Exposes Google Sheets operations (read, write, append) via the Google Sheets API using **OAuth 2.0** authentication.

## Requirements

- **Google Cloud project** with Sheets API enabled
- **OAuth 2.0 Client ID** configured (Web application or Desktop app)
- **Client Secrets JSON** downloaded from Google Cloud Console

## Configuration

| Attribute             | Description                                                         | Required |
| --------------------- | ------------------------------------------------------------------- | -------- |
| `client_secrets_json` | OAuth2 Client Secrets JSON (string or object)                       | Yes      |
| `redirect_uri`        | Redirect URI for OAuth2 flow (default: `urn:ietf:wg:oauth:2.0:oob`) | No       |

## Agent wiring (agent.yaml)

```yaml
- action: jvagent/google_sheets_action
  context:
    client_secrets_json: ${GOOGLE_CLIENT_SECRETS_JSON}
```

Set `GOOGLE_CLIENT_SECRETS_JSON` in `.env` as a JSON string containing the downloaded credentials.

## Endpoints

| Method | Path                                         | Description                            |
| ------ | -------------------------------------------- | -------------------------------------- |
| GET    | `/agents/{agent_id}/google_sheets/auth_url`  | Get the OAuth2 authorization URL       |
| POST   | `/agents/{agent_id}/google_sheets/authorize` | Exchange authorization code for tokens |
| GET    | `/agents/{agent_id}/google_sheets/read`      | Read data from a specific range        |
| POST   | `/agents/{agent_id}/google_sheets/write`     | Write data explicitly to a range       |
| POST   | `/agents/{agent_id}/google_sheets/append`    | Append data to the end of a sheet      |

### Authorization Flow

1. Call `GET /agents/{agent_id}/google_sheets/auth_url` to receive the authorization URL.
2. Direct the user to the URL to grant permissions.
3. The user will receive an authorization code.
4. Call `POST /agents/{agent_id}/google_sheets/authorize` with the code to complete setup:

```json
POST /agents/{agent_id}/google_sheets/authorize
{
  "code": "4/0A..."
}
```

_Note: Tokens are automatically cached securely using native action file storage (`token.json`). If tokens expire, they will be refreshed automatically if a refresh token was granted._

### Read

```http
GET /agents/{agent_id}/google_sheets/read?spreadsheet_id=1abc...&range_name=Sheet1!A1:D10
```

### Write (Overwrite)

```json
POST /agents/{agent_id}/google_sheets/write
{
  "spreadsheet_id": "1abc...",
  "range_name": "Sheet1!A1:B2",
  "values": [
    ["Header 1", "Header 2"],
    ["Data 1", "Data 2"]
  ]
}
```

### Append

```json
POST /agents/{agent_id}/google_sheets/append
{
  "spreadsheet_id": "1abc...",
  "range_name": "Sheet1!A:A",
  "values": [
    ["New Row Data 1", "New Row Data 2"]
  ]
}
```
