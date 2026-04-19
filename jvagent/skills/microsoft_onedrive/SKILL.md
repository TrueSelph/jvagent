---
name: microsoft_onedrive
description: Upload, share, and manage OneDrive files.
requires-actions:
  - MicrosoftOneDriveAction
allowed-tools:
  - upload_file
  - delete_file
  - list_files
  - share_file
version: 1
tags:
  - storage
  - microsoft
---

## Workflow

1. Determine the OneDrive operation the user needs (upload, delete, list, or share).
2. Use the appropriate OneDrive tool to perform the operation.
3. Format the results clearly for the user.

### Constraints

- Always confirm with the user before deleting files.
- For `upload_file`, provide either `content` (text content) or `source_url` (URL to download from).