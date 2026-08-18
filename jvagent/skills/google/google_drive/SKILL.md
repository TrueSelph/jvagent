---
name: google_drive
description: >-
  Manage Google Drive files. Use when the user asks to list, upload,
  download, share, or delete Drive files, or inspect file metadata.
requires-actions:
  - GoogleDriveAction
  - MCPAction
  - MCPOAuthAction
allowed-tools:
  - google_drive__list_files
  - google_drive__upload_file
  - google_drive__get_file_metadata
  - google_drive__get_shared_drive_metadata
  - google_drive__get_media
  - google_drive__share_file
  - google_drive__delete_file
---

# Google Drive

Use these tools for Drive files. Pass **only** the parameters listed for each
tool. Omit unused optionals. Do not invent argument names.

Confirm before `google_drive__delete_file`. For a shared-drive root (folder IDs
starting with `0A`), use `google_drive__get_shared_drive_metadata` instead of
listing it as a regular folder.

## Auth

If a tool fails because credentials are missing or expired, tell the operator
to open `/api/mcp/google_workspace/auth?service=drive`. Do not send users to
`/api/google/...`. Do not ask the visitor to paste tokens.

## Tool parameters

### google_drive__list_files

- `folder_id` (optional) — omit for root
- `with_link` (optional)
- `depth` (optional) — subfolder recursion, default 5
- `drive_id` (optional) — required when `folder_id` is on a shared drive

### google_drive__upload_file

- `name` (required)
- `content` (optional) — base64 file bytes; use this or `source_url`
- `source_url` (optional) — download URL; use this or `content`
- `mime_type` (optional)
- `parent_folder_id` (optional)

### google_drive__get_file_metadata

- `file_id` (required)
- `fields` (optional) — comma-separated Drive fields, default `id, name, mimeType`

### google_drive__get_shared_drive_metadata

- `drive_id` (required)

### google_drive__get_media

- `file_id` (required)

### google_drive__share_file

- `file_id` (required)
- `share_type` (optional) — `link` or `user`
- `link_scope` (optional) — `anyone` or `domain` (when `share_type` is `link`)
- `email` (optional) — required when `share_type` is `user`
- `role` (optional) — `reader`, `writer`, or `owner`

### google_drive__delete_file

- `file_id` (required)
