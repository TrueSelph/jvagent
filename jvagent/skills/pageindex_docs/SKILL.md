---
name: pageindex_docs
description: List, ingest, and remove document-based content from your knowledge base.
requires-actions:
  - PageIndexAction
allowed-tools:
  - pageindex_docs__list_documents
  - pageindex_docs__assimilate
  - pageindex_docs__delete_document
version: 1
tags:
  - pageindex
  - documents
  - management
---

## Workflow

1. Determine the document management operation the user needs (list, ingest, or remove).
2. Use the appropriate tool to perform the operation.
3. Format the results clearly for the user.

### Listing Documents

- Use `pageindex_docs__list_documents` to see what documents are in the index.
- Optionally filter by `collection_name` or `metadata_filter`.

### Ingesting Documents

- Use `pageindex_docs__assimilate` to add a document to the PageIndex index.
- Provide the file path or URL as `doc`.
- Set `doc_name` to give the document a recognizable name.
- Optionally set `doc_description`, `doc_url`, or `metadata` for richer indexing.

### Removing Documents

- Always confirm with the user before deleting a document.
- Use `pageindex_docs__delete_document` with the `doc_name` to remove a document and all its chunks.

### Constraints

- Always confirm with the user before deleting documents.
- For `pageindex_docs__assimilate`, the `doc` parameter is a file path (e.g., `/path/to/file.pdf`) or URL.
- Ingestion is an async operation that may take time for large documents.

## Scope

This skill is for PageIndex document lifecycle management: list indexed docs, ingest new docs, and delete docs. Use it for maintaining index contents. Do not use it for answering content questions directly when retrieval (`pageindex_search`) is the better fit.

## Grounding

- Only report document names, statuses, and counts that tools return.
- If listing shows no matching documents, say that explicitly rather than assuming indexing succeeded.
- Always confirm before `pageindex_docs__delete_document`, including the exact `doc_name`.