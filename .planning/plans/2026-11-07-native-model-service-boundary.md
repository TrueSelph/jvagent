# Reference model service retirement boundary

Source revision `50b6c737`. This is an offline compatibility audit. The
reference agent selects GPT-4.1 through LiteLLM for orchestration and uses
GPT-4.1 Nano through OpenAI for PageIndex tree search and Reply. MCP is disabled
in this declaration and still carries an OpenAI sampling configuration.
OpenAI, Ollama and LiteLLM Actions are all enabled. The model service migration
guard in `jvagent/cli/harness_migrate.py:459` rejects multiple declarations
before checking consumer coverage or the selected service settings. Removing
alternate declarations alone would leave the existing consumers unresolved.

| Surface | Current dependency | Retirement condition |
|---|---|---|
| Orchestrator | Selected LiteLLM Action | Existing native GPT-4.1 execution and explicit offline conversion |
| Reply | Compose selects a model Action (`reply_action.py:244`); native publication uses the supplied output (`:383`) | Preserve literal native publication and inventory any independent compose callers before removing the service |
| PageIndex direct retrieval | The reviewed read profile exposes direct search, and inherited search still resolves a model Action (`pageindex_action.py:450`) | Keep existing search name, graph scope and access filtering. Direct retrieval must work with zero provider requests |
| PageIndex tree search | Action search binds the Action in a ContextVar; the HTTP endpoint dispatches retrieval directly (`endpoints.py:1857`). The retriever can invoke model completion (`retrieval.py:479`) | Route both entrypoints through explicit host-selected SDK execution. Preserve ranked results, citations and access scope |
| PageIndex ingestion and sync | Action assimilation and Drive sync resolve a model Action (`pageindex_action.py:565`, `pageindex_google_drive_sync_action.py:1438`). PDF processing crosses a worker thread (`documents.py:670`) | Carry explicit execution policy across the worker boundary. Bound all completions, including continuation history, and preserve cancellation and publication semantics |
| PageIndex jvforge callback | Authenticated callback resolves a PageIndex Action and accepts text or image work (`endpoints.py:2011`, `pageindex_action.py:216`) | Preserve callback response/usage fields, admit only configured models and media, and bound and account for the operation independently of chat |
| MCP sampling | Legacy sampling resolves a model Action (`mcp_action.py:701`); native reviewed toolsets use public SDK lifecycle (`:886`) | Treat disabled sampling, reviewed stdio tool use and active sampling as separate compatibility cases. Qualify or block active sampling before retiring its provider |
| Other modular declarations | Converter consumer coverage is explicit and incomplete | Review lifecycle, tools and endpoints individually. An unclassified Action is not evidence that it calls a model |

## Reproduced behavior

Executed against the installed source with Python 3.11. Provider and retrieval
boundaries were stubs. No credentials, browser sessions or persisted graph were
used. The focused PageIndex and migration suite passed 231 tests with one skip.

| Probe | Observation |
|---|---|
| Bind a model Action whose async query raises, then call `llm_acompletion_with_usage` with a fallback stub | One Action attempt, one direct fallback attempt, returned total tokens **0** even though fallback returned text |
| Supply nonempty assistant history to `llm_completion` with the same binding | One direct fallback attempt, zero additional Action attempts. The exact history and finish-reason flag reached the fallback |
| Read the bound Action from the default `run_in_executor` worker | Binding absent inside the worker, matching the PDF worker dispatch mechanism |
| Invoke `search_documents_endpoint` with tree search and stub retrieval | One tree-search dispatch and zero Action lookups. This observes the endpoint handoff rather than a provider call |

The bridge's async failure fallback and sync history bypass are at
`jvagent/action/pageindex/llm_bridge.py:118` and `:142`. The direct implementation
constructs its own provider client and retry loop in
`jvagent/action/pageindex/core/utils.py:68` and `:124`. Merely replacing
`get_model_action()` leaves these paths outside native usage limits, persistence
and provider receipts.

## Implementation and qualification order

1. Introduce an explicit model completion boundary for model-assisted PageIndex
   operations, backed by public Pydantic AI model execution. Keep the retrieval
   and indexing algorithms in PageIndex. Require a host-admitted model and bound
   when the native profile requests completion. Preserve the service's text,
   history, finish reason and usage contract with a thin adapter. Native mode
   must propagate provider failure without invoking the old direct client.
2. Bind tool-origin completion to the parent run's shared budget, ownership,
   cancellation and durable SDK receipts. Independently authenticated
   administrative operations need their own bounded run and accounting policy.
   Preserve the worker boundary explicitly and verify that cancellation prevents
   further provider requests and publication. A thread-local or ContextVar
   binding alone cannot establish that contract across the PDF executor.
3. Qualify tree search first through the existing skill and the HTTP search
   entrypoint, with populated and tenant-excluded data, actual provider use,
   failure/limit probes and persisted accounting. Then qualify ingestion,
   callback text and image handling, and active sampling separately. Count
   service requests in addition to outer chat requests. The four observed
   bypasses above become regressions at their normal entrypoints.
4. Extend the offline converter only for qualified configurations and preserve
   Action declarations and existing index data. Remove superseded provider
   branches, bridge fallback and global patches after the last supported caller
   is migrated. Keep the direct retrieval profile and any explicit lexical
   fallback separately identifiable from provider fallback.

In a separate offline copy of the reference YAML, pinning the already reviewed
PDF image alone left both original blockers. Removing the two custom legacy
resource overrides in favor of the reviewed native preset cleared the script
declaration blocker while leaving the model-service blocker and producing no
candidate. This does not qualify the combined app, its script workload or its
retained state. The checked-in reference declaration remains the baseline for
the [two-blocker assessment](2026-11-07-native-sdk-tool-policy-reference-blockers.json).
