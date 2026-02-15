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
- `PageIndexRetrievalInteractAction` – InteractAction for agent workflows
- REST endpoints under `/pageindex/`

## Configuration

### PageIndexRetrievalInteractAction (agent config)

**Context** (attributes): `doc_name`, `limit`, `weight`, `strategy`, `model`, `directive`, `parameters`, etc.

**Config block** (ingestion): Settings applied when documents are assimilated. Use the `config` section in agent.yaml:

| Config key | Type | Default | Description |
|------------|------|---------|-------------|
| `node_summary` | bool | false | Generate node summaries during ingestion (required for tree_search) |
| `node_text` | bool | true | Add node text to structure |
| `doc_description` | bool | false | Add document description |
| `max_token_num_each_node` | Optional[int] | None | Max tokens per node (PDF only) |
| `summary_token_threshold` | Optional[int] | 200 | Token threshold for summaries (Markdown only) |
| `max_node_tokens` | Optional[int] | - | Alias for `summary_token_threshold` |

**Retrieval config** (also in `config`): `limit`, `strategy`, `model`, etc.

| Attribute | Type | Default | Description |
|-----------|------|---------|-------------|
| `doc_name` | Optional[str] | None | Scope search to a single document |
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

### Environment Variables (database and LLM)

| Variable | Description | Default |
|----------|-------------|---------|
| `JVSPATIAL_PAGEINDEX_DB_PATH` | Path for json/sqlite | `{parent_of_prime_db}/pageindex` |
| `JVSPATIAL_PAGEINDEX_DB_NAME` | MongoDB database name / DynamoDB table name | pageindex_db |
| `JVSPATIAL_PAGEINDEX_DB_TYPE` | json, sqlite, mongodb, dynamodb | json |
| `JVSPATIAL_JSONDB_PATH` | Prime DB path (derives shared root) | - |
| `JVSPATIAL_SQLITE_PATH` | Prime DB sqlite path | jvdb/sqlite/jvspatial.db |
| `PAGEINDEX_TREE_SEARCH_MODEL` | LLM for tree_search | gpt-4o-mini |
| `CHATGPT_API_KEY` / `OPENAI_API_KEY` | API key for tree_search | - |

## REST API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/pageindex/documents` | Ingest PDF/MD (multipart: file, doc_name, model) |
| GET | `/pageindex/documents` | List documents |
| GET | `/pageindex/documents/{doc_name}` | Get document metadata |
| DELETE | `/pageindex/documents/{doc_name}` | Delete document |
| POST | `/pageindex/documents/search` | Search (query, doc_name, strategy, limit) |

## Example Agent Configuration

```yaml
- action: jvagent/pageindex_retrieval_interact_action
  context:
    enabled: true
    weight: -75
    anchors:
      - "User asks a question about indexed documents"
  config:
    limit: 10
    strategy: "tree_search"
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
- tree_search requires CHATGPT_API_KEY or OPENAI_API_KEY; falls back to direct if missing
- PageIndex DB path is sibling of prime DB (e.g. `./pageindex` next to `./jvagent_db`)
- Use `model_action_type` for token tracking and observability in agent context
- **Ingestion config**: Put `node_summary`, `node_text`, `doc_description`, etc. under the `config` block (not `context`). These apply when documents are assimilated via API or `assimilate_document()`.
