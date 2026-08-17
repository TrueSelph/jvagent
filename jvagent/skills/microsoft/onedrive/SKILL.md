---
name: onedrive
description: >-
  Manage OneDrive files. Use when the user asks to list, upload, share, or
  delete OneDrive files.
requires-actions:
  - MicrosoftOneDriveAction
  - MCPAction
  - MCPOAuthAction
allowed-tools:
  - onedrive__list_files
  - onedrive__upload_file
  - onedrive__share_file
  - onedrive__delete_file
---

# OneDrive

Use these tools for OneDrive files. Pass **only** the parameters listed for
each tool. Omit unused optionals. Do not invent argument names.

Confirm before `onedrive__delete_file`.

## Auth

If a tool fails because credentials are missing or expired, tell the operator
to open `/api/mcp/microsoft_365/auth?service=onedrive`. Do not send users to
`/api/microsoft/...`. Do not ask the visitor to paste tokens.

## Tool parameters

### onedrive__list_files

- `folder_id` (optional) — omit for root
- `with_link` (optional)
- `depth` (optional) — subfolder recursion, default 5

### onedrive__upload_file

- `name` (required)
- `content` (optional) — base64 file bytes; use this or `source_url`
- `source_url` (optional) — download URL; use this or `content`
- `mime_type` (optional)
- `parent_folder_id` (optional)

### onedrive__share_file

- `file_id` (required)
- `share_type` (optional) — `link` or `user`
- `link_scope` (optional) — `anyone` or `organization` (when `share_type` is `link`)
- `email` (optional) — required when `share_type` is `user`
- `role` (optional) — `read` or `write`

### onedrive__delete_file

- `file_id` (required)
