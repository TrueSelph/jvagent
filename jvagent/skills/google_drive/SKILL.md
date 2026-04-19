---
name: google_drive
description: Upload, share, and manage Google Drive files.
requires-actions:
  - GoogleDriveAction
allowed-tools:
  - google_drive__upload_file
  - google_drive__delete_file
  - get_file_metadata
  - google_drive__list_files
  - google_drive__share_file
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
- For `google_drive__upload_file`, provide either `content` (text content) or `source_url` (URL to download from).
- Default file metadata fields are `id, name, mimeType` unless the user specifies otherwise.

## Scope

This skill is for Google Drive file storage tasks: upload, list, metadata lookup, sharing, download, and deletion. Use it when the user needs Drive file management. Do not use it for spreadsheet cell edits, calendar scheduling, or email content operations.

## Grounding

- Only report file IDs, names, permissions, and metadata that are returned by Drive tools.
- If no files are returned, say that explicitly rather than implying files exist.
- Always confirm before `google_drive__delete_file` and before broad sharing actions.