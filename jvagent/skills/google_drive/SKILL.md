---
name: google_drive
description: Upload, share, and manage Google Drive files.
requires-actions:
  - GoogleDriveAction
allowed-tools:
  - upload_file
  - delete_file
  - get_file_metadata
  - list_files
  - share_file
  - get_media
version: 1
tags:
  - storage
  - google
---

## Workflow

1. Determine the Google Drive operation the user needs (upload, delete, list, share, get metadata, or download media).
2. Use the appropriate Drive tool to perform the operation.
3. Format the results clearly for the user.

### Constraints

- Always confirm with the user before deleting files.
- For `upload_file`, provide either `content` (text content) or `source_url` (URL to download from).
- Default file metadata fields are `id, name, mimeType` unless the user specifies otherwise.