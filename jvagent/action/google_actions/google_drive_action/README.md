# Google Drive Action

Exposes Google Drive operations (upload, list, share) via the Google Drive API using **OAuth 2.0** authentication.

## Requirements

- **Google Cloud project** with Drive API enabled
- **OAuth 2.0 Client ID** configured (Web application or Desktop app)
- **Client Secrets JSON** downloaded from Google Cloud Console

## Configuration

| Attribute             | Description                                                         | Required |
| --------------------- | ------------------------------------------------------------------- | -------- |
| `client_secrets_json` | OAuth2 Client Secrets JSON (string or object)                       | Yes      |
| `redirect_uri`        | Redirect URI for OAuth2 flow (default: `urn:ietf:wg:oauth:2.0:oob`) | No       |
| `default_parent_id`   | Default parent folder ID for uploads                                | No       |

## Agent wiring (agent.yaml)

```yaml
- action: jvagent/google_drive_action
  context:
    client_secrets_json: ${GOOGLE_CLIENT_SECRETS_JSON}
    default_parent_id: ${GOOGLE_DRIVE_PARENT_FOLDER_ID}
```

Set the variables in your `.env` file:

```env
# Google web Oauth
GOOGLE_CLIENT_SECRETS_JSON={"web":{"client_id":"433423825197.apps.googleusercontent.com","project_id":"jvagent","auth_uri":"https://accounts.google.com/o/oauth2/auth","token_uri":"https://oauth2.googleapis.com/token","auth_provider_x509_cert_url":"https://www.googleapis.com/oauth2/v1/certs","client_secret":"GOCSPX","redirect_uris":["https://9cc9-190-93-39-3.ngrok-free.app/api/google/callback/"]}}
# Allow insecure transport for local testing if not using HTTPS locally
OAUTHLIB_INSECURE_TRANSPORT = '1'
# Allow scopes to change (if user doesn't grant all permissions)
OAUTHLIB_RELAX_TOKEN_SCOPE = '1'
GOOGLE_DRIVE_PARENT_FOLDER_ID = 1wjA2BC1APlkt3RMTHtotDOu

```

### Setup Instructions

1. Go to the [Google Cloud Console](https://console.cloud.google.com/).
2. Select your project and navigate to **APIs & Services > Credentials**.
3. Click **Create Credentials > OAuth client ID**.
4. Choose **Web application** (or Desktop app).
5. Add your authorized redirect URIs (e.g., `http://localhost:8080/` or `urn:ietf:wg:oauth:2.0:oob`).
6. Click **Create**, then download the JSON containing your client secrets.
7. Minify the downloaded JSON and paste it into `GOOGLE_CLIENT_SECRETS_JSON` in your `.env` file. Be sure to also configure the `GOOGLE_REDIRECT_URI` to exactly match what was configured in step 5. (https://console.cloud.google.com/apis/credentials)

## Endpoints

| Method | Path                                        | Description                                |
| ------ | ------------------------------------------- | ------------------------------------------ |
| GET    | `/agents/{agent_id}/google_drive/auth_url`  | Get the OAuth2 authorization URL           |
| POST   | `/agents/{agent_id}/google_drive/authorize` | Exchange authorization code for tokens     |
| POST   | `/agents/{agent_id}/google_drive/upload`    | Upload file (from URL or base64 content)   |
| GET    | `/agents/{agent_id}/google_drive/list`      | List files in a folder                     |
| POST   | `/agents/{agent_id}/google_drive/share`     | Share file (get link or grant user access) |

### Authorization Flow

1. Call `GET /agents/{agent_id}/google_drive/auth_url` to receive the authorization URL.
2. Direct the user to the URL to grant permissions.
3. The user will receive an authorization code.
4. Call `POST /agents/{agent_id}/google_drive/authorize` with the code to complete setup:

```json
POST /agents/{agent_id}/google_drive/authorize
{
  "code": "4/0A..."
}
```

_Note: Tokens are automatically cached securely using native action file storage (`token.json`). If tokens expire, they will be refreshed automatically if a refresh token was granted._

### Upload

Provide either `source_url` (fetch from URL) or `content` (base64):

```json
POST /agents/{agent_id}/google_drive/upload
{
  "name": "document.pdf",
  "source_url": "https://example.com/file.pdf",
  "parent_folder_id": "optional-override"
}
```

Or with base64 content:

```json
{
  "name": "notes.txt",
  "content": "SGVsbG8gV29ybGQ=",
  "mime_type": "text/plain"
}
```

### List

```http
GET /agents/{agent_id}/google_drive/list?folder_id=root&page_size=20
```

Omit `folder_id` to use `default_parent_id` or root.

### Share

Get shareable link (optionally make public):

```json
POST /agents/{agent_id}/google_drive/share
{
  "file_id": "1abc...",
  "share_type": "link",
  "link_scope": "anyone"
}
```

`link_scope`: `anyone`, `domain`, `organization`, or omit for restricted (existing access only).

Grant access to a user:

```json
{
  "file_id": "1abc...",
  "share_type": "user",
  "email": "user@example.com",
  "role": "reader"
}
```

`role`: `reader`, `writer`, `commenter`.
