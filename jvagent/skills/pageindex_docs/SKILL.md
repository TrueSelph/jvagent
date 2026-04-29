---
name: pageindex_docs
description: List, ingest, update, and remove document-based content from your knowledge base.
requires-actions:
  - PageIndexAction
allowed-tools:
  - pageindex_docs__list_documents
  - pageindex_docs__assimilate
  - pageindex_docs__delete_document
version: 1
plan-steps:
  - Perform the document operation (list / ingest / delete / update)
tags:
  - pageindex
  - documents
  - management
---

## Workflow

1. Determine the document management operation the user needs (list, ingest, update, or remove).
2. Use the appropriate tool to perform the operation.
3. Format the results clearly for the user.

### Listing Documents

- Use `pageindex_docs__list_documents` to see what documents are in the index.
- Optionally filter by `collection_name` or `metadata_filter`.

### Ingesting Documents

- Use `pageindex_docs__assimilate` to add a document to the PageIndex index.
- Provide `doc` as an **HTTPS URL**, an **absolute path** on the host (e.g. bundled corpus), or a path **relative to the user’s jvspatial sandbox** (preferred for files produced or uploaded for this user). Relative paths load from sandbox storage first.
- Set `doc_name` to give the document a recognizable name.
- Optionally set `doc_description`, `doc_url`, or `metadata` for richer indexing.

### Write-then-Assimilate

- When the user asks you to generate/write a file and then ingest it, treat those as separate tracked steps.
- Before calling `pageindex_docs__assimilate`, confirm the file exists by listing the write location with `fileinterface__list_directory`.
- Pass the sandbox-relative file path as `doc` (for example: `GGI_report.md`) and set `doc_name` explicitly (for example: `"GGI Report"`).

### On Failure

- If `pageindex_docs__assimilate` returns an error, call `fileinterface__list_directory` to confirm the file is present in the expected location.
- If present, retry once using the exact path you confirmed from the directory listing.
- If it still fails, mark the step as skipped with `task_tracker(action="skip", reason=...)` and report the specific error to the user.

### Removing Documents

- Always confirm with the user before deleting a document.
- Use `pageindex_docs__delete_document` with the `doc_name` to remove a document and all its chunks.

### Updating Documents

- To update a document, you must first delete the existing version and then ingest the updated one.
- 1. Use `pageindex_docs__delete_document` with the `doc_name`.
- 2. Use `pageindex_docs__assimilate` to ingest the updated content using the same `doc_name`.

### Constraints

- Always confirm with the user before deleting documents.
- When updating a document, you MUST successfully delete the existing document before assimilating the new version to avoid duplication.
- For `pageindex_docs__assimilate`, prefer sandbox-relative paths for user artifacts; use absolute paths only when reading fixed app/corpus files. URLs must use `http://` or `https://`.
- Ingestion is an async operation that may take time for large documents.

## Scope

This skill is for PageIndex document lifecycle management: list indexed docs, ingest new docs, and delete docs. Use it for maintaining index contents. Do not use it for answering content questions directly when retrieval (`pageindex_search`) is the better fit.

## Grounding

- Only report document names, statuses, and counts that tools return.
- If listing shows no matching documents, say that explicitly rather than assuming indexing succeeded.
- Always confirm before `pageindex_docs__delete_document`, including the exact `doc_name`.