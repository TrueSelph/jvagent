# Google Drive Action

Manage Google Drive files and folders using OAuth 2.0 authentication. Supports uploading, listing, sharing, and deleting files with recursive folder traversal.

## Features

- **Upload files** from URL or base64 content
- **List files** recursively with configurable depth
- **Share files** via link or direct user access
- **Delete files** from Google Drive
- **Compare file changes** between snapshots
- **Automatic token refresh** with secure caching

## Requirements

- **Google Cloud project** with Drive API enabled
- **OAuth 2.0 Client ID** configured (Web application)
- **Client Secrets JSON** downloaded from Google Cloud Console

## Configuration

| Attribute             | Description                                                         | Required |
| --------------------- | ------------------------------------------------------------------- | -------- |
| `client_secrets_json` | OAuth2 Client Secrets JSON (string or object)                       | Yes      |
| `redirect_uri`        | Redirect URI for OAuth2 flow (default: `urn:ietf:wg:oauth:2.0:oob`) | No       |
| `default_parent_id`   | Default parent folder ID for uploads (default: `root`)              | No       |

## Agent Configuration (agent.yaml)

```yaml
- action: jvagent/google_drive_action
  context:
    client_secrets_json: ${GOOGLE_CLIENT_SECRETS_JSON}
    default_parent_id: ${GOOGLE_DRIVE_PARENT_FOLDER_ID}
```

Set environment variables in `.env`:

```env
# Google OAuth 2.0 Client Secrets (Web application)
GOOGLE_CLIENT_SECRETS_JSON={"web":{"client_id":"YOUR_CLIENT_ID.apps.googleusercontent.com","project_id":"YOUR_PROJECT","auth_uri":"https://accounts.google.com/o/oauth2/auth","token_uri":"https://oauth2.googleapis.com/token","auth_provider_x509_cert_url":"https://www.googleapis.com/oauth2/v1/certs","client_secret":"YOUR_CLIENT_SECRET","redirect_uris":["https://YOUR_DOMAIN/api/google/callback/"]}}

# Default parent folder ID for uploads
GOOGLE_DRIVE_PARENT_FOLDER_ID=root

# For local testing only (not recommended for production)
OAUTHLIB_INSECURE_TRANSPORT=1
OAUTHLIB_RELAX_TOKEN_SCOPE=1
```

## Setup Instructions

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create or select a project
3. Enable the **Google Drive API**:
   - Navigate to **APIs & Services > Library**
   - Search for "Google Drive API"
   - Click **Enable**
4. Create OAuth 2.0 credentials:
   - Go to **APIs & Services > Credentials**
   - Click **Create Credentials > OAuth client ID**
   - Choose **Web application**
   - Add authorized redirect URIs (e.g., `https://YOUR_DOMAIN/api/google/callback/`)
   - Click **Create**
5. Download the JSON credentials
6. Minify the JSON and set as `GOOGLE_CLIENT_SECRETS_JSON` in `.env`
7. Add test user here `https://console.cloud.google.com/auth/audience?project=jvagent`

## Endpoints

| Method | Path                                        | Description                                |
| ------ | ------------------------------------------- | ------------------------------------------ |
| GET    | `/agents/{agent_id}/google_drive/auth_url`  | Get the OAuth2 authorization URL           |
| POST   | `/agents/{agent_id}/google_drive/authorize` | Exchange authorization code for tokens     |
| POST   | `/agents/{agent_id}/google_drive/upload`    | Upload file (from URL or base64 content)   |
| GET    | `/agents/{agent_id}/google_drive/list`      | List files in a folder                     |
| POST   | `/agents/{agent_id}/google_drive/share`     | Share file (get link or grant user access) |
| DELETE | `/agents/{agent_id}/google_drive/delete`    | Delete a file                              |

## Authorization Flow

1. Call `GET /agents/{agent_id}/google_drive/auth_url` to receive the authorization URL
2. Direct the user to the URL to grant permissions
3. The user will receive an authorization code
4. Call `POST /agents/{agent_id}/google_drive/authorize` with the code:

```json
POST /agents/{agent_id}/google_drive/authorize
{
  "code": "4/0A..."
}
```

Tokens are automatically cached securely using action file storage (`token.json`). Expired tokens are refreshed automatically if a refresh token was granted.

## API Usage

### Upload File

Upload from URL:

```json
POST /agents/{agent_id}/google_drive/upload
{
  "name": "document.pdf",
  "source_url": "https://example.com/file.pdf",
  "parent_folder_id": "optional-folder-id"
}
```

Upload from base64 content:

```json
POST /agents/{agent_id}/google_drive/upload
{
  "name": "notes.txt",
  "content": "SGVsbG8gV29ybGQ=",
  "mime_type": "text/plain",
  "parent_folder_id": "optional-folder-id"
}
```

### List Files

List files in a folder with recursive traversal:

```http
GET /agents/{agent_id}/google_drive/list?folder_id=root&depth=5&with_link=true
```

Query parameters:
- `folder_id`: Folder ID to list (default: `default_parent_id` or `root`)
- `depth`: Recursion depth for nested folders (default: `5`)
- `with_link`: Include shareable links in response (default: `false`)

### Share File

Get shareable link (make public):

```json
POST /agents/{agent_id}/google_drive/share
{
  "file_id": "1abc...",
  "share_type": "link",
  "link_scope": "anyone",
  "role": "reader"
}
```

Grant access to a user:

```json
POST /agents/{agent_id}/google_drive/share
{
  "file_id": "1abc...",
  "share_type": "user",
  "email": "user@example.com",
  "role": "reader"
}
```

Parameters:
- `link_scope`: `anyone`, `domain`, `organization` (for link sharing)
- `role`: `reader`, `writer`, `commenter`

### Delete File

```json
DELETE /agents/{agent_id}/google_drive/delete
{
  "file_id": "1abc..."
}
```

## Response Examples

### Upload Response

```json
{
  "id": "1abc123xyz",
  "name": "document.pdf"
}
```

### List Response

```json
{
  "files": [
    {
      "id": "1folder123",
      "name": "My Folder",
      "mimeType": "application/vnd.google-apps.folder",
      "createdTime": "2026-03-10T10:00:00Z",
      "modifiedTime": "2026-03-10T10:00:00Z",
      "url": "https://drive.google.com/drive/folders/1folder123",
      "files": [
        {
          "id": "1file456",
          "name": "document.pdf",
          "mimeType": "application/pdf",
          "createdTime": "2026-03-10T11:00:00Z",
          "modifiedTime": "2026-03-10T11:00:00Z",
          "url": "https://drive.google.com/file/d/1file456/view"
        }
      ]
    }
  ]
}
```

### Share Response

```json
{
  "webViewLink": "https://drive.google.com/file/d/1abc123xyz/view?usp=sharing"
}
```

## Error Handling

Common errors and solutions:

| Error | Cause | Solution |
| ----- | ----- | -------- |
| `invalid_grant` | Authorization code expired or invalid | Request a new authorization URL |
| `insufficient_permissions` | Scopes not granted | Re-authorize with proper scopes |
| `notFound` | File or folder doesn't exist | Verify the file/folder ID |
| `forbidden` | No access to file/folder | Check permissions in Google Drive |

## Best Practices

- Store credentials securely in environment variables
- Use `default_parent_id` to organize uploads
- Implement pagination for large file lists
- Cache authorization tokens to reduce API calls
- Monitor API quota usage in Google Cloud Console
- Use appropriate `mime_type` for uploads
- Test with OAuth2 user-delegated credentials (or your org’s approved Google auth pattern) for production
