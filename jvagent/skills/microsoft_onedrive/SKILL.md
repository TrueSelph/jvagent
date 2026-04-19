---
name: microsoft_onedrive
description: Upload, share, and manage OneDrive files.
requires-actions:
  - MicrosoftOneDriveAction
allowed-tools:
  - microsoft_onedrive__upload_file
  - microsoft_onedrive__delete_file
  - microsoft_onedrive__list_files
  - microsoft_onedrive__share_file
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
- For `microsoft_onedrive__upload_file`, provide either `content` (text content) or `source_url` (URL to download from).

## Scope

This skill is for OneDrive file operations: upload, list, share, and delete. Use it when the user is managing files in Microsoft OneDrive. Do not use it for Excel cell edits, Outlook mail, or calendar workflows.

## Grounding

- Only report file names, IDs, and share results that are returned by OneDrive tools.
- If listing returns no files, explicitly state that no matching files were found.
- Always confirm before `microsoft_onedrive__delete_file` or broad sharing operations.