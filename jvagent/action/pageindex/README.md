# PageIndex Action

Vectorless RAG for document indexing and retrieval. No embeddings, no vector store. Uses PageIndex-style hierarchical document structure with LLM-based tree search.

## Overview

Unlike RetrievalInteractAction (which uses a vector store), PageIndex uses reasoning-based tree traversal and LLM selection of relevant nodes. Documents are parsed into a hierarchical structure, persisted to a jvspatial graph database, and retrieved via tree search, direct text filtering, or graph walker strategies.

## Key Features

- **PDF and Markdown ingestion** with hierarchical structure extraction
- **Two-stage retrieval**: Lexical index (BM25) for candidate selection, then strategy-specific refinement—scales to large document bases
- **Three retrieval strategies**: `tree_search` (LLM reasoning, recommended), `direct` (regex/text filter), `walker` (graph traversal)
- **Document reference metadata** – page numbers, document name, and source URL rendered as numbered citations for LLM responses
- **jvagent LLM bridge** for observability and token tracking when used in agent context
- **REST API** for ingestion, listing, search, and deletion
- **Persists structure** to jvspatial graph database (sibling of prime DB)

## Architecture

### Execution Flow

```
Ingestion: PDF/MD -> PageIndex core (page_index/md_to_tree) -> tree_to_graph -> jvspatial + lexical index
Retrieval: query -> lexical candidates (BM25) -> tree_search/direct/walker -> directive -> PersonaAction
```

### Two-Stage Retrieval

Retrieval uses a **lexical index** (inverted index with BM25 scoring) for fast candidate selection, then applies the chosen strategy (tree_search, direct, walker) to that subset:

1. **Stage 1**: Tokenize query, fetch posting lists for terms, score nodes with BM25, return top-K candidates.
2. **Stage 2**: Strategy-specific refinement—tree_search runs LLM selection on top documents; direct hydrates candidates by ID; walker traverses top-ranked roots.

This scales to large document bases without full-corpus scans. When the lexical index has no data (e.g. documents ingested before the feature), retrieval falls back to the original behavior.

### Components

- `assimilate_document()` – ingestion (programmatic); builds lexical index during persist
- `search_documents()` – retrieval (programmatic)
- `PageIndexRetrievalInteractAction` – InteractAction for agent workflows
- `lexical_index` – inverted index (tokenizer, ranking, index CRUD)
- REST endpoints under `/pageindex/`

## Configuration

### PageIndexRetrievalInteractAction (agent config)

**Context** (attributes): `doc_name`, `limit`, `weight`, `strategy`, `model`, `directive`, `parameters`, etc. Retrieval params can also be in `config`; both take effect (config overrides attributes when present).

**Config block** (ingestion + retrieval): Use the `config` section in agent.yaml. Ingestion settings apply when documents are assimilated. Retrieval params (`limit`, `strategy`, `model`, `doc_name`) can be in `config` or `context` (attributes).

| Config key | Type | Default | Description |
|------------|------|---------|-------------|
| `node_summary` | bool | false | Generate node summaries during ingestion. Config can override. Required for tree_search. |
| `node_text` | bool | true | Add node text to structure |
| `doc_description` | bool | false | Add document description |
| `max_token_num_each_node` | Optional[int] | 20000 | Max tokens per node (PDF only) |
| `summary_token_threshold` | Optional[int] | 200 | Token threshold for summaries (Markdown only) |
| `max_node_tokens` | Optional[int] | - | Alias for `summary_token_threshold` |
| `limit` | int | 10 | Number of results to retrieve (retrieval) |
| `strategy` | str | "tree_search" | Retrieval strategy (retrieval) |
| `include_references` | bool | true | Render numbered source references (page numbers, URLs) in directive. Set false to save tokens. |
| `collection` | Optional[str] | null | Override collection name (default: agent_id) |
| `metadata_filter` | Optional[Dict] | null | Key-value filter to narrow search by document metadata |
| `enable_lexical_index` | bool | true | Use two-stage retrieval (BM25 candidates). Set false to disable. |
| `candidate_k` | int | 200 | Max candidates from lexical index per query |
| `max_docs_for_tree_search` | int | 10 | Max documents to include in tree_search (lexical-ranked) |
| `max_summary_chars` | Optional[int] | 300 | Max chars per node summary in tree prompt |
| `max_tree_prompt_tokens` | Optional[int] | 16000 | Max tokens for tree; exceeding triggers fallback to direct |

| Attribute | Type | Default | Description |
|-----------|------|---------|-------------|
| `doc_name` | Optional[str] | None | Scope search to a single document |
| `collection` | Optional[str] | None | Override collection (default: agent_id) |
| `metadata_filter` | Optional[Dict] | None | Filter by document metadata |
| `limit` | int | 10 | Number of results to retrieve |
| `include_references` | bool | true | Render numbered source references in directive; set false to save tokens |
| `weight` | int | -75 | Execution order (after InteractRouter) |
| `strategy` | str | "tree_search" | "tree_search", "direct", or "walker" |
| `model` | Optional[str] | None | LLM for tree_search (else PAGEINDEX_TREE_SEARCH_MODEL or gpt-4o-mini) |
| `model_action_type` | str | "OpenAILanguageModelAction" | LanguageModelAction for observability |
| `max_summary_chars` | Optional[int] | None | Max chars per node summary in tree prompt |
| `max_tree_prompt_tokens` | Optional[int] | None | Max tokens for tree; exceeding triggers fallback to direct |
| `directive` | str | DIRECTIVE_TEMPLATE | Template with `{results}` and `{references}` placeholders (when include_references) |
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
| `doc_url` | Optional[str] | None | Source URL of the document (stored on DocumentRootNode for reference citations) |

### Environment Variables (database and LLM)

| Variable | Description | Default |
|----------|-------------|---------|
| `JVAGENT_APP_ID` | Overrides app node's app_id when set | app node's app_id |
| `JVAGENT_PAGEINDEX_DB_TYPE` | json, sqlite, mongodb, dynamodb | json |
| `JVAGENT_PAGEINDEX_DB_PATH` | Path for json/sqlite (explicit) | - |
| `JVAGENT_PAGEINDEX_DB_ROOT` | Root for path when DB_PATH not set | . |
| `JVAGENT_PAGEINDEX_DB_NAME` | Explicit db name (overrides autogeneration) | - |
| `JVAGENT_PAGEINDEX_DB_URI` | MongoDB connection URI | mongodb://localhost:27017 |
| `JVAGENT_PAGEINDEX_DB_TABLE_NAME` | DynamoDB table name | derived from db_name |
| `JVAGENT_PAGEINDEX_DB_REGION` | AWS region for DynamoDB | us-east-1 |
| `PAGEINDEX_TREE_SEARCH_MODEL` | LLM for tree_search | gpt-4o-mini |
| `CHATGPT_API_KEY` / `OPENAI_API_KEY` | API key for tree_search | - |

**DB name resolution** (when `JVAGENT_PAGEINDEX_DB_NAME` is unset): `{app_id}_pageindex_db` — one db per app; multiple agents share it, documents scoped by collection (agent_id). Fallback: `config.pageindex.db_name` in app.yaml, else `pageindex_db`.

## REST API Endpoints

All routes are agent-scoped (collection = agent_id from path).

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/agents/{agent_id}/pageindex/documents` | Ingest PDF/MD (multipart: file, doc_name, doc_url, metadata) |
| GET | `/api/agents/{agent_id}/pageindex/documents` | List documents (query: metadata) |
| GET | `/api/agents/{agent_id}/pageindex/documents/{doc_name}` | Get document metadata |
| DELETE | `/api/agents/{agent_id}/pageindex/documents/{doc_name}` | Delete document |
| POST | `/api/agents/{agent_id}/pageindex/documents/search` | Search (body: query, doc_name, strategy, limit, metadata). Returns results with start_index, end_index, doc_url. |

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

## Document Reference Metadata

When `include_references` is true (default), retrieval results include page numbers and document URLs so the LLM can cite sources. The directive formats numbered excerpts with a reference list. Each reference uses the format `[N] doc_name, pp. X-Y. url` (comma between document name and page range, period before URL).

- **Page numbers**: From `DocumentNode` (`start_index`, `end_index`, `physical_index`) – populated during PDF/Markdown ingestion
- **Document URL**: Set at ingestion via `doc_url` parameter or REST form field `doc_url`; stored on `DocumentRootNode`. Also supports `metadata.url` as fallback
- **References section**: Rendered only when page numbers or URLs are available. If no reference metadata exists, the section is omitted entirely
- **Disable references**: Set `include_references: false` in config to use the plain directive format and save tokens

## Example Agent Configuration

```yaml
- action: jvagent/pageindex_retrieval_interact_action
  context:
    enabled: true
    weight: -75
    # collection: my_custom_collection   # Optional override; default = agent_id
    anchors:
      - "User asks a question about indexed documents"
  config:
    limit: 10
    strategy: "tree_search"
    include_references: true   # Set false to save tokens (plain directive, no citations)
    # metadata_filter: {"access": "internal"}  # Optional: narrow search by metadata
    node_summary: true      # Required for tree_search; generates summaries during ingestion
    node_text: true
    doc_description: false
    max_token_num_each_node: 20000   # PDF only
    summary_token_threshold: 200      # Markdown only
    # model: gpt-4o-mini  # Optional override
    # enable_lexical_index: true   # Two-stage retrieval (default: true)
    # candidate_k: 200            # Max lexical candidates per query
    # max_docs_for_tree_search: 10  # Max documents in tree_search
```

## Usage

- **Basic setup**: Ingest documents via API or `assimilate_document()`, add action to agent
- **Query selection**: Uses `interaction.utterance` (preferred) or `interaction.interpretation`
- **Directive format**: With `include_references: true`, numbered excerpts plus a References section (document name, page range, URL). With `include_references: false`, plain flat format. References section is omitted when no page/URL metadata exists
- **Integration**: Runs after InteractRouter, adds directive for PersonaAction

## Retrieval Strategies

All strategies use the lexical index when available (documents ingested after the feature). When the index has no data, they fall back to the original full-scan behavior.

- **tree_search**: Lexical index ranks documents; LLM selects nodes from top-N document trees. Recommended. Requires API key.
- **direct**: Lexical candidates hydrated by ID; fallback to regex scan on title/text/summary. No LLM.
- **walker**: Lexical index ranks documents; traverses top-N roots. No LLM.

## Dependencies

- jvspatial (graph DB)
- tiktoken, openai, PyPDF2, pymupdf, python-dotenv, pyyaml
- LanguageModelAction (for tree_search observability)

## Troubleshooting / Best Practices

- Documents must be ingested before retrieval
- **Lexical index and re-ingestion**: The lexical index is built during ingestion. Documents ingested *before* the two-stage retrieval feature will work (graceful fallback) but will not benefit from BM25 candidate selection. To get the scaling benefits, re-ingest those documents (delete and assimilate again) or use `lexical_index.reindex_nodes()` to rebuild the index from existing graph nodes.
- tree_search requires CHATGPT_API_KEY or OPENAI_API_KEY; falls back to direct if missing
- PageIndex DB path defaults to `{JVAGENT_PAGEINDEX_DB_ROOT}/{db_name}` when `JVAGENT_PAGEINDEX_DB_PATH` is unset
- Use `model_action_type` for token tracking and observability in agent context
- **Ingestion config**: Put `node_summary`, `node_text`, `doc_description`, etc. under the `config` block (not `context`). These apply when documents are assimilated via API or `assimilate_document()`. REST ingestion uses config pushed when the action registers; if no agent has PageIndex, defaults apply.
- **Reference citations**: Provide `doc_url` at ingestion (REST form field or `assimilate_document(doc_url=...)`) for URLs in references. Page numbers come from PDF structure automatically. Set `include_references: false` to reduce token usage when citations are not needed.
- **Web vs WhatsApp consistency**: If page numbers or references appear on one channel (e.g. WhatsApp) but not another (e.g. web), ensure both use the **same agent_id** and that the agent has PageIndex with `include_references: true`. Different agents or configs per channel will produce different responses. The reference format is `[N] doc_name, pp. X-Y. url` (comma between doc name and page range, period before URL).
