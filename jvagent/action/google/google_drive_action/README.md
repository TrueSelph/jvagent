# Google Drive Action

Manage Google Drive files and folders. **Login is MCP OAuth** (`MCPOAuthToken`), not `GoogleToken` / `/api/google/{action_id}`.

## Features

- **Upload files** from URL or base64 content
- **List files** recursively with configurable depth
- **Share files** via link or direct user access
- **Delete files** from Google Drive
- **Compare file changes** between snapshots
- **Automatic token refresh** via MCP OAuth

## Requirements

- **Google Cloud project** with Drive API enabled
- **OAuth 2.0 Client ID** (Web application) with redirect URI `{JVAGENT_PUBLIC_BASE_URL}/api/mcp/google_workspace/auth/callback`
- **`GOOGLE_CLIENT_SECRETS_JSON`** in `.env`
- Agent also has `jvagent/mcp_oauth` and `jvagent/mcp` (`google_workspace`)

## Create credentials

This is an **OAuth client JSON** (client id + secret). It is **not** a service-account key from [IAM & Admin → Service Accounts](https://console.cloud.google.com/iam-admin/serviceaccounts).

1. Open [Google Cloud Console → Credentials](https://console.cloud.google.com/apis/credentials) (APIs & Services → Credentials). Do **not** use IAM & Admin → Service Accounts.
2. Select or create the Google Cloud project.
3. Enable **Google Drive API**: [APIs & Services → Library](https://console.cloud.google.com/apis/library).
4. Configure the **OAuth consent screen** (APIs & Services → OAuth consent screen). Choose Internal or External as appropriate. If the app is in Testing, add the Google accounts that will sign in as test users.
5. Click **Create credentials** → **OAuth client ID**.
6. Application type: **Web application**. Name it (for example `jvagent-mcp`).
7. Under **Authorized redirect URIs**, add exactly:

   `{JVAGENT_PUBLIC_BASE_URL}/api/mcp/google_workspace/auth/callback`

   The host must match the public base URL used at runtime (for example an ngrok HTTPS origin). Do **not** use the old `/api/google/callback/` path.
8. Click **Create**. Download the JSON (client id + secret).
9. Put the file path or the JSON contents in `.env` as `GOOGLE_CLIENT_SECRETS_JSON`, and set `JVAGENT_PUBLIC_BASE_URL` (see below).
10. Authorize at `{JVAGENT_PUBLIC_BASE_URL}/api/mcp/google_workspace/auth?account=integral&service=drive`.

## Set `.env`

```bash
JVAGENT_PUBLIC_BASE_URL=https://your-public-host.example
# Path to the downloaded OAuth client JSON:
GOOGLE_CLIENT_SECRETS_JSON=/absolute/path/to/client_secret.json
# Or paste the file contents as a single-line JSON string:
# GOOGLE_CLIENT_SECRETS_JSON={"web":{"client_id":"...","client_secret":"...","redirect_uris":["https://your-public-host.example/api/mcp/google_workspace/auth/callback"]}}
# Optional default folder for uploads:
# GOOGLE_DRIVE_PARENT_FOLDER_ID=root
```

`redirect_uris` in the downloaded JSON / Cloud Console must be the **MCP** callback (`/api/mcp/google_workspace/auth/callback`), not `/api/google/callback/`.

## Agent Configuration (agent.yaml)

```yaml
- action: jvagent/mcp_oauth
  context:
    enabled: true

- action: jvagent/mcp
  context:
    enabled: true
    servers:
      - name: google_workspace
        enabled: true
        transport: stdio
        command: npx
        args: ["-y", "@aaronsb/google-workspace-mcp"]
        tools: ["manage_drive"]
        show_tools: false
        sandbox_mode: false

- action: jvagent/google_drive_action
  context:
    enabled: true
```

Set `GOOGLE_CLIENT_SECRETS_JSON` in `.env`. Authorize at **`/api/mcp/google_workspace/auth?service=drive`**. Optional `GOOGLE_DRIVE_PARENT_FOLDER_ID` for uploads.

## Authorization

1. Open `{JVAGENT_PUBLIC_BASE_URL}/api/mcp/google_workspace/auth?account=integral&service=drive`.
2. Complete Google consent. The callback stores `MCPOAuthToken`.
3. Drive tools use that token. Re-auth if refresh fails.

## Endpoints

Admin REST handlers under `/actions/{action_id}/...` (upload, list, share, delete) authenticate with the MCP OAuth token.

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
