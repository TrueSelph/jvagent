# PageIndex Action

Vectorless RAG for document indexing and retrieval. No embeddings, no vector store. Uses PageIndex-style hierarchical document structure with LLM-based tree search.

## Overview

Unlike RetrievalInteractAction (which uses a vector store), PageIndex uses reasoning-based tree traversal and LLM selection of relevant nodes. Documents are parsed into a hierarchical structure, persisted to a jvspatial graph database, and retrieved via tree search, direct text filtering, or graph walker strategies.

## Key Features

- **PDF and Markdown ingestion** with hierarchical structure extraction
- **Three retrieval strategies**: `tree_search` (LLM reasoning, recommended), `direct` (regex/text filter), `walker` (graph traversal)
- **jvagent LLM bridge** for observability and token tracking when used in agent context
- **REST API** for ingestion, listing, search, and deletion
- **Persists structure** to jvspatial graph database (sibling of prime DB)

## Architecture

### Execution Flow

```
Ingestion: PDF/MD -> PageIndex core (page_index/md_to_tree) -> tree_to_graph -> jvspatial
Retrieval: query -> tree_search/direct/walker -> directive -> PersonaAction
```

### Components

- `assimilate_document()` – ingestion (programmatic)
- `search_documents()` – retrieval (programmatic)
- `PageIndexAction` – InteractAction for agent workflows
- REST endpoints under `/pageindex/`

## Configuration

### PageIndexAction (agent config)

**Context** (attributes): `doc_name`, `limit`, `weight`, `strategy`, `model`, `directive`, `parameters`, etc. Retrieval params can also be in `config`; both take effect (config overrides attributes when present).

**Config block** (ingestion + retrieval): Use the `config` section in agent.yaml. Ingestion settings apply when documents are assimilated. Retrieval params (`limit`, `strategy`, `model`, `doc_name`) can be in `config` or `context` (attributes).

| Config key | Type | Default | Description |
|------------|------|---------|-------------|
| `node_summary` | bool | true | Generate node summaries during ingestion (required for tree_search) |
| `node_text` | bool | true | Add node text to structure |
| `doc_description` | bool | false | Add document description |
| `max_token_num_each_node` | Optional[int] | 20000 | Max tokens per node (PDF only) |
| `summary_token_threshold` | Optional[int] | 200 | Token threshold for summaries (Markdown only) |
| `max_node_tokens` | Optional[int] | - | Alias for `summary_token_threshold` |
| `limit` | int | 10 | Number of results to retrieve (retrieval) |
| `strategy` | str | "tree_search" | Retrieval strategy (retrieval) |
| `collection` | Optional[str] | null | Override collection name (default: agent_id) |
| `metadata_filter` | Optional[Dict] | null | Key-value filter to narrow search by document metadata |

| Attribute | Type | Default | Description |
|-----------|------|---------|-------------|
| `doc_name` | Optional[str] | None | Scope search to a single document |
| `collection` | Optional[str] | None | Override collection (default: agent_id) |
| `metadata_filter` | Optional[Dict] | None | Filter by document metadata |
| `limit` | int | 10 | Number of results to retrieve |
| `weight` | int | -75 | Execution order (after InteractRouter) |
| `strategy` | str | "tree_search" | "tree_search", "direct", or "walker" |
| `model` | Optional[str] | None | LLM for tree_search (else PAGEINDEX_TREE_SEARCH_MODEL or gpt-4o-mini) |
| `model_action_type` | str | "OpenAILanguageModelAction" | LanguageModelAction for observability |
| `directive` | str | DIRECTIVE_TEMPLATE | Template with `{results}` placeholder |
| `parameters` | List[Dict] | [...] | Conditional behavioral rules |

### assimilate_document (programmatic)

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `doc` | str/Path/bytes | - | File path or bytes |
| `doc_name` | Optional[str] | None | Override document name |
| `model` | Optional[str] | None | LLM for tree generation |
| `model_action` | Optional[Any] | None | LanguageModelAction for observability |
| `if_add_node_id` | str | "yes" | Add node_id to structure |
| `if_add_node_text` | str | "yes" | Add text to nodes (None = use action config) |
| `if_add_node_summary` | str | "no" | Add summaries yes/no (None = use action config) |
| `if_add_doc_description` | str | "no" | Add doc description yes/no (None = use action config) |
| `toc_check_page_num` | Optional[int] | None | Pages to check for TOC (PDF) |
| `max_page_num_each_node` | Optional[int] | None | Max pages per node |
| `max_token_num_each_node` | Optional[int] | config | Max tokens per node (PDF; None = use action config) |
| `summary_token_threshold` | Optional[int] | 200 | Token threshold for summaries (Markdown; None = use action config) |
| `persist` | bool | True | Persist to graph DB |
| `collection_name` | str | "default" | Collection this document belongs to (typically agent_id) |
| `metadata` | Optional[Dict] | None | Custom key-value metadata for filtering at query time |

### Environment variables

#### PageIndex database

Read by `get_pageindex_config()` in `jvagent.action.pageindex.config`. See the PAGEINDEX block in [`.env.example`](../../../.env.example) at the jvagent repository root.

| Variable | Description | Default |
|----------|-------------|---------|
| `JVAGENT_APP_ID` | When `JVAGENT_PAGEINDEX_DB_NAME` is unset, db name becomes `{sanitized_app_id}_pageindex_db` | From app config / `.env` |
| `JVAGENT_PAGEINDEX_DB_TYPE` | Backend: `json`, `sqlite`, `mongodb`, `dynamodb` | `json` |
| `JVAGENT_PAGEINDEX_DB_PATH` | **json**: base directory for files. **sqlite**: database file path. When set, used as-is for that backend. | — |
| `JVAGENT_PAGEINDEX_DB_ROOT` | When `JVAGENT_PAGEINDEX_DB_PATH` is unset: **json** uses `{root}/{resolved_db_name}`; **sqlite** uses `{root}/{resolved_db_name}/sqlite/pageindex.db` | `.` |
| `JVAGENT_PAGEINDEX_DB_NAME` | Overrides auto-derived name; MongoDB database name; DynamoDB table name if `JVAGENT_PAGEINDEX_DB_TABLE_NAME` unset | `pageindex_db`, or `{app_id}_pageindex_db`, or `pageindex.db_name` in app YAML |
| `JVAGENT_PAGEINDEX_DB_URI` | MongoDB connection URI | `mongodb://localhost:27017` |
| `JVAGENT_PAGEINDEX_DB_TABLE_NAME` | DynamoDB table name | Resolved db name |
| `JVAGENT_PAGEINDEX_DB_REGION` | DynamoDB region | `us-east-1` |
| `JVAGENT_PAGEINDEX_DB_ENDPOINT_URL` | DynamoDB custom endpoint (optional) | — |

#### jvspatial prime graph database (not PageIndex)

These configure the **main** jvspatial graph database (agents, core graph, etc.). PageIndex **does not** read them for its document store; set `JVAGENT_PAGEINDEX_*` above if you want a specific PageIndex location.

| Variable | Description | Typical default |
|----------|-------------|-----------------|
| `JVSPATIAL_DB_PATH` | File DB root path for **json** / **sqlite** prime graph (with `JVSPATIAL_DB_TYPE`) | `./jvagent_db` / `./jvdb` (see jvspatial docs) |

#### LLM (`tree_search`)

| Variable | Description | Default |
|----------|-------------|---------|
| `PAGEINDEX_TREE_SEARCH_MODEL` | Model for tree_search | `gpt-4o-mini` |
| `OPENAI_API_KEY` | API key for tree_search | — |

## REST API Endpoints

All routes are agent-scoped (collection = agent_id from path).

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/agents/{agent_id}/pageindex/documents` | Ingest PDF/MD (multipart: file, doc_name, metadata) |
| GET | `/api/agents/{agent_id}/pageindex/documents` | List documents (query: metadata) |
| GET | `/api/agents/{agent_id}/pageindex/documents/{doc_name}` | Get document metadata |
| DELETE | `/api/agents/{agent_id}/pageindex/documents/{doc_name}` | Delete document |
| POST | `/api/agents/{agent_id}/pageindex/documents/search` | Search (body: query, doc_name, strategy, limit, metadata) |

## Named Collections and Multi-Agent

Documents are scoped by **collection** (default: `agent_id`). When multiple agents share one jvagent app, each agent's PageIndex action uses its own collection, keeping documents isolated.

- **Collection resolution**: `collection` attribute → `config.collection` → `agent_id` → `"default"`
- **Agent-scoped REST**: Use `/api/agents/{agent_id}/pageindex/*` so the path defines the collection
- **Override**: Set `collection: my_custom_collection` in context/config for shared collections

## Custom Metadata

Documents can have key-value metadata at ingestion; filter at query time.

- **Ingestion**: `assimilate_document(..., metadata={"topic": "finance", "year": 2024})` or REST form field `metadata` (JSON string)
- **Search/List**: `metadata_filter={"topic": "finance"}` or REST `metadata` param (JSON)
- **Values**: str, int, float, bool, or list of primitives; multiple keys use AND semantics

## Example Agent Configuration

```yaml
- action: jvagent/pageindex_action
  context:
    enabled: true
    weight: -75
    # collection: my_custom_collection   # Optional override; default = agent_id
    anchors:
      - "User asks a question about indexed documents"
  config:
    limit: 10
    strategy: "tree_search"
    # metadata_filter: {"access": "internal"}  # Optional: narrow search by metadata
    node_summary: true      # Required for tree_search; generates summaries during ingestion
    node_text: true
    doc_description: false
    max_token_num_each_node: 20000   # PDF only
    summary_token_threshold: 200      # Markdown only
    # model: gpt-4o-mini  # Optional override
```

## Usage

- **Basic setup**: Ingest documents via API or `assimilate_document()`, add action to agent
- **Query selection**: Uses `interaction.utterance` (preferred) or `interaction.interpretation`
- **Directive format**: Default template with `{results}` placeholder
- **Integration**: Runs after InteractRouter, adds directive for PersonaAction

## Retrieval Strategies

- **tree_search**: Builds tree from graph, sends to LLM with query, parses node_list, fetches content. Recommended. Requires API key.
- **direct**: Database find with regex on title/text/summary. No LLM.
- **walker**: DocumentWalker traversal from roots. No LLM.

## Dependencies

- jvspatial (graph DB)
- tiktoken, openai, PyPDF2, pymupdf, python-dotenv, pyyaml
- LanguageModelAction (for tree_search observability)

## Troubleshooting / Best Practices

- Documents must be ingested before retrieval
- tree_search requires `OPENAI_API_KEY` (or a configured model action); falls back to direct if missing
- Colocating PageIndex with the prime DB (e.g. `./pageindex_db` next to `./jvagent_db`) is optional and **not** inferred from `JVSPATIAL_DB_PATH`; set `JVAGENT_PAGEINDEX_DB_PATH` or `JVAGENT_PAGEINDEX_DB_ROOT` to match the layout you want
- Use `model_action_type` for token tracking and observability in agent context
- **Ingestion config**: Put `node_summary`, `node_text`, `doc_description`, etc. under the `config` block (not `context`). These apply when documents are assimilated via API or `assimilate_document()`. REST ingestion uses config pushed when the action registers; if no agent has PageIndex, defaults apply.
