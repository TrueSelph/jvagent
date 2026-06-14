# Postiz Action

The `PostizAction` provides a structured interface for interacting with the Postiz Public API. It allows the agent to list social media integrations, upload media, and create synchronous or scheduled posts.

## Configuration

To use this action, configure it in your `agent.yaml`:

```yaml
actions:
  - action: jvagent/postiz_action
    context:
      enabled: true
      api_key: ${POSTIZ_API_KEY}
      base_url: "http://localhost:4007/api/public/v1"
```

### Attributes

| Attribute | Type | Default | Description |
|-----------|------|---------|-------------|
| `api_key` | `str` | `""` | The Public API key from Postiz (Settings > Developers). |
| `base_url` | `str` | `".../v1"`| The base URL of the Postiz Public API. |
| `timeout` | `int` | `30` | Timeout for API requests in seconds. |

## Methods

### `list_integrations()`
Retrieves a list of all connected social media channels.
- **Returns**: `List[Dict]` containing channel details (ID, name, type).

### `list_integrations_summary()`
Returns a human-readable string summary of connected channels.
- **Returns**: `str` (e.g., "- My Facebook (facebook) [ID: 123]")

### `upload_media(file_path: str)`
Uploads a local file to Postiz storage.
- **Returns**: `Dict` containing the media `id` and `path`.
- **Note**: Files must have valid extensions supported by Postiz.

### `create_post(content, integrations, publish_date=None, media=None)`
Creates or schedules a post across one or multiple integrations.
- **`content`**: Post text.
- **`integrations`**: List of channel IDs.
- **`publish_date`**: Optional ISO-8601 string. If omitted, uses `app.now()`.
- **`media`**: Optional list of objects `[{"id": "...", "path": "..."}]`.

## Authentication

### List Compatible Providers
Before initiating authentication, you can retrieve a list of all platforms supported by the Postiz instance.

- **Endpoint**: `GET /api/postiz/providers`
- **Returns**: A JSON object with a `providers` list (e.g., `[{"id": "x", "name": "X (Twitter)"}, ...]`).

### Programmatic Provider Connection
Postiz integrations typically require an OAuth flow. You can initiate this programmatically via the included endpoint:

- **Endpoint**: `GET /api/postiz/auth/{provider}`
- **Provider**: `facebook`, `linkedin`, `x`, `instagram`, `tiktok`, `discord`, etc.
- **Returns**: A JSON object with a `url` to visit.

Example:
```bash
curl -X GET http://localhost:8000/api/postiz/auth/linkedin
# Response: {"url": "https://api.postiz.com/auth/social/linkedin?token=..."}
```

Visit the returned URL in your browser to authorize the account. Once authorized, it will appear in `list_integrations()`.

## Timezone Support
This action uses the `App.now()` utility to determine the default publication date for "now" posts, ensuring that the Postiz instance receives timestamps aligned with your agent's configured timezone.
