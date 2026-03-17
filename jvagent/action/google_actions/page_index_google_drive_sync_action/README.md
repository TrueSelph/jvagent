# Page Index Google Drive Sync Action

Automatically sync and ingest documents from Google Drive folders into PageIndex for vectorless RAG (Retrieval-Augmented Generation). Monitors folders for changes and updates the document index accordingly.

## Features

- **Automatic document ingestion** from Google Drive folders
- **Change detection** (added, modified, removed files)
- **Incremental updates** to avoid re-processing unchanged documents
- **Metadata attachment** to ingested documents
- **Recursive folder traversal** with configurable depth
- **Automatic cleanup** of deleted documents
- **Integration with PageIndex** for semantic search

## Requirements

- **Google Drive API** enabled and configured
- **GoogleDriveAction** available in the agent
- **PageIndexRetrievalInteractAction** available for document indexing
- **OAuth 2.0 credentials** for Google Drive access

## Configuration

| Attribute              | Description                                                                    | Required |
| ---------------------- | ------------------------------------------------------------------------------ | -------- |
| `google_drive_folders` | List of folder configs with `folder_id` and optional `metadata`                | No       |
| `client_secrets_json`  | OAuth2 Client Secrets JSON (inherited from GoogleDriveAction)                 | Yes      |
| `default_parent_id`    | Default parent folder ID (inherited from GoogleDriveAction)                   | No       |

## Agent Configuration (agent.yaml)

```yaml
- action: jvagent/google_drive_action
  context:
    client_secrets_json: ${GOOGLE_CLIENT_SECRETS_JSON}
    default_parent_id: ${GOOGLE_DRIVE_PARENT_FOLDER_ID}

- action: jvagent/page_index_google_drive_sync_action
  context:
    google_drive_folders:
      - folder_id: "1syTF0gsEjsl7DhjxrnPuTdmwwNDoh8dj"
        metadata:
          source: "company_docs"
          category: "faq"
      - folder_id: "1another_folder_id"
        metadata:
          source: "policies"
          category: "internal"
```

## Setup Instructions

1. Ensure **GoogleDriveAction** is configured with OAuth 2.0 credentials
2. Ensure **PageIndexRetrievalInteractAction** is configured with a model and collection
3. Identify Google Drive folder IDs to monitor:
   - Open folder in Google Drive
   - Copy the folder ID from the URL: `https://drive.google.com/drive/folders/{FOLDER_ID}`
4. Add folder configurations to `google_drive_folders` in agent.yaml
5. Optionally add metadata to tag documents by source or category

## Endpoints

| Method | Path                                                    | Description                                    |
| ------ | ------------------------------------------------------- | ---------------------------------------------- |
| POST   | `/agents/{agent_id}/page_index_google_drive_sync/ingest` | Trigger document ingestion from configured folders |

## API Usage

### Trigger Document Ingestion

Ingest documents from all configured folders:

```json
POST /agents/{agent_id}/page_index_google_drive_sync/ingest
{
  "remove_deleted_documents": false
}
```

Ingest from specific folders:

```json
POST /agents/{agent_id}/page_index_google_drive_sync/ingest
{
  "google_drive_folders": [
    {
      "folder_id": "1syTF0gsEjsl7DhjxrnPuTdmwwNDoh8dj",
      "metadata": {
        "source": "company_docs",
        "category": "faq"
      }
    }
  ],
  "remove_deleted_documents": true
}
```

Parameters:
- `google_drive_folders`: Override configured folders (optional)
- `remove_deleted_documents`: Delete documents from index when removed from Drive (default: `false`)

### Response

```json
{
  "status": "completed",
  "message": "Documents ingested successfully!",
  "documents_ingested": {
    "added": [
      "FAQ_2026.pdf",
      "Policies.docx"
    ],
    "updated": [
      "Guidelines_v2.pdf"
    ],
    "to_be_removed": [
      "Archived_FAQ.pdf"
    ]
  }
}
```

## How It Works

1. **Folder Monitoring**: Retrieves current file list from configured Google Drive folders
2. **Change Detection**: Compares with previously ingested files to identify:
   - **Added**: New files to ingest
   - **Modified**: Files with changes to re-ingest
   - **Removed**: Files deleted from Drive
3. **Document Ingestion**:
   - Downloads files from Google Drive
   - Extracts text and generates embeddings
   - Stores in PageIndex collection with metadata
4. **State Persistence**: Saves folder state to track changes across runs

## Supported File Types

- PDF (`.pdf`)
- Word Documents (`.docx`, `.doc`)
- Text Files (`.txt`)
- Markdown (`.md`)
- Other formats supported by PageIndex

## Metadata Attachment

Metadata is attached to all documents ingested from a folder:

```yaml
google_drive_folders:
  - folder_id: "1syTF0gsEjsl7DhjxrnPuTdmwwNDoh8dj"
    metadata:
      source: "company_docs"
      category: "faq"
      department: "support"
      version: "2026-03"
```

Metadata is queryable in PageIndex for filtering and context enrichment.

## Best Practices

- **Organize folders**: Use separate folders for different document categories
- **Add metadata**: Tag documents by source, category, or department for better retrieval
- **Schedule syncs**: Run ingestion periodically (e.g., daily) to keep index updated
- **Monitor changes**: Review the response to understand what was added/updated/removed
- **Test first**: Start with a small folder to verify configuration
- **Backup**: Keep original documents in Google Drive as backup
- **Permissions**: Ensure service account has read access to all monitored folders

## Troubleshooting

| Issue | Cause | Solution |
| ----- | ----- | -------- |
| No documents ingested | Folder is empty or inaccessible | Verify folder ID and permissions |
| Documents not searchable | PageIndex not configured | Ensure PageIndexRetrievalInteractAction is enabled |
| Duplicate documents | Re-running ingestion | Check change detection logic |
| Memory issues | Large files or many documents | Process folders in batches |
| Slow ingestion | Large documents or slow network | Consider splitting large files |

## Integration with PageIndex Retrieval

Once documents are ingested, use PageIndexRetrievalInteractAction to search:

```yaml
- action: jvagent/pageindex_retrieval_interact_action
  context:
    enabled: true
    collection: "agent_name"
    strategy: "tree_search"
    node_summary: true
```

Documents ingested via this action are automatically available for semantic search and RAG.
