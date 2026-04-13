# Microsoft OneDrive Action

Read and write the signed-in user’s **OneDrive** (default drive) via **Microsoft Graph** (`/me/drive/...`), with **Entra ID** OAuth on `MicrosoftAction`.

Shared OAuth and env vars: [Microsoft actions README](../README.md).

## Microsoft Graph scopes

Delegated scopes:

- `offline_access`
- `User.Read`
- `Files.ReadWrite.All`

## Environment (optional)

| Variable | Purpose |
| -------- | ------- |
| `ONEDRIVE_PARENT_FOLDER_ID` | Default folder for list/upload when `folder_id` / `parent_folder_id` is omitted: `root` or a drive **item** id |

## Agent wiring (`agent.yaml`)

```yaml
- action: jvagent/microsoft_onedrive_action
```

## REST API (unified drive)

Paths assume `/api` prefix. Admin-authenticated; see OpenAPI for bodies.

| Method | Path | Description |
| ------ | ---- | ----------- |
| GET | `/api/microsoft/{action_id}` | OAuth landing page |
| GET | `/api/actions/{action_id}/list` | Recursive file tree under `folder_id` (default from `ONEDRIVE_PARENT_FOLDER_ID` or `root`): `with_link` for `webUrl` |
| POST | `/api/actions/{action_id}/upload` | Upload file or create folder: `name`; for files add `content` (base64) or `source_url`, optional `mime_type`, `parent_folder_id` |
| DELETE | `/api/actions/{action_id}/delete` | `file_id` — removes drive item |
| POST | `/api/actions/{action_id}/share` | `file_id`, `share_type` (`link` or `user`), `link_scope`, `email`, `role` — uses `createLink` / `invite` |
| POST | `/api/actions/{action_id}/compare_files` | Diff two nested listings (`added` / `removed` / `modified`) |

## Behavior notes

- **Folder create**: Call upload with `name` only (no `content` / `source_url`); creates a folder with rename-on-conflict.
- **File upload**: PUT to `/{parent}:/{filename}:/content` with decoded bytes from base64 `content` or bytes fetched from `source_url`.
- **Folders** in list output use MIME type `application/vnd.microsoft.graph.folder`.
