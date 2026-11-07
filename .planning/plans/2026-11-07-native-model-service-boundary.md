# Reference model service retirement boundary

## WhatsApp messaging provider boundary

WhatsApp messaging can be preserved during legacy chat-provider
retirement. Its ingress spawns the shared interaction walker
(`whatsapp/utils/endpoint_helpers.py:574`), STT resolves its separately
configured Action (`:472`), and its response-bus adapter sends the
supplied content using the configured WhatsApp transport
(`whatsapp/whatsapp_adapter.py:65`, `:171`). Native replies still
require verified sender and interaction scope (`:90`). The converter
retains the complete transport declaration, including template
allowlists and TTS wiring. This review covers the model-service
dependency only. Live Meta transport, legacy prompt parity and
recipient delivery remain separate qualification gates.

## Voice provider boundary and SkillHub catalog gap

WhatsApp Voice delegates accepted calls to the configured jvvoice
service with its own credentials and JV Agent callback target
(`whatsapp_voice_action.py:202`, `:248`). It can be preserved during
selected chat-provider retirement. The offline regression verifies
that its declaration survives unchanged. This does not qualify live
Meta or jvvoice transport.

The SkillHub install helper recognizes only the retired
SkillInteractAction selector (`_installer.py:138`, `:266`).
A disposable YAML probe showed byte-identical native and current
Orchestrator declarations after an attempted install registration.
The same probe updated a SkillInteractAction positive control. Native
catalog entries come from explicit directory declarations
(`native_harness_action.py:399`). Before admitting SkillHub, decide how
operator-reviewed installs and removals update the native catalog
without granting new tools or introducing implicit discovery.
The probe touched neither the registry nor a running graph.

## Reviewed provider-independent services

The offline converter can preserve CodeExecution, FileInterface and
ElevenLabs TTS while retiring a qualified selected chat model service.
CodeExecution declares its SDK sandbox separately
(`code_execution_action.py:84`), FileInterface publishes independently
scoped file tools (`file_interface_action.py:110`), and ElevenLabs
synthesizes supplied text through its own SDK and credentials
(`elevenlabs.py:34`). The converter still validates enabled script
policy separately. A regression checks the three declarations survive
unchanged when the selected model service is retired.

On the script-qualified reference copy, the CLI now reports five Actions
awaiting review, plus the additional OpenAI and Ollama declarations.
PageIndex still selects the old OpenAI service for its legacy tree
search and ingestion paths. Intro contributes a first-turn Reply
parameter and needs a native presentation review. No reference
candidate was generated. This round changed offline conversion only.

## Reference script policy rehearsal

At `244c2332` the unchanged reference migration reported two blockers.
A disposable copy retained the complete skill list and all Actions. Its
CodeExecution declaration kept the 90-second timeout, 64,000-character
output cap and disabled network, replaced the legacy unlimited memory
and 60-second CPU settings with the native fixed resource preset, and
declared local image
`sha256:8906f76b96018319b876bffd2c6f839a3491bb3b0b63957bb40acd00012ed1ed`.
The image layers the repository PDF recipe onto the existing DOCX
qualification image. Network-free, non-root, read-only-root image probes
generated PDF and DOCX bytes and ran the bundled triage sorter. The
offline planner admitted the revised script declaration at source digest
`sha256:4f3dfd18ca563334cb17c08005331b42b9e8add7ecb410fa7d223b7ebeffe060`.
Only the aggregate model-service blocker remained. No candidate was
generated. The script probes exercised container dependencies directly.
Browser/SDK execution of the combined profile, bounded storage,
compatibility of other DOCX modes and retained-state cutover remain open.

The offline CLI report now separates additional model services from
Actions whose model-service independence has not been reviewed
(`harness_migrate.py:315`). Against the same script-qualified copy,
the selected service is LiteLLM. OpenAI and Ollama are additional
declarations. The unreviewed Actions are CodeExecution, ElevenLabs TTS,
FileInterface, IntroInteract, PageIndex, SkillHub, WhatsApp and
WhatsApp Voice. The list reports review status rather than model
calls. No runtime settings are modified and no candidate is generated.
The regression verifies that Action configuration values are omitted.
This is offline CLI qualification only.

At `3e0d5d4c`, a reduced fixture with source digest
`sha256:3da34b31edf72affce99eb9c28519d1da4eedcf505153f5faea103cbcf91779a`
converted a 1 ms provider timeout. Merge sync preserved the existing
120-second setting. After applying the converted settings to the
disposable Action and verifying PostgreSQL readback, a fresh jvchat
conversation attempted GPT-4.1 with SDK retries disabled.
Interaction `n.Interaction.62770a0bb529471d8a691e13` persisted
`ModelTimeoutError` after 196 ms end to end, with one failed attempt
lasting 8 ms, no tool events, no provider usage receipt, and one
unknown-cost event. jvchat showed the failure response and the unknown
cost. This verifies request timeout enforcement on the native OpenAI
path. The fixture does not isolate the transport phase that timed out.
The failure copy asks users to check configuration and does not name
the timeout. The full reference conversion remains blocked.

An isolated candidate rehearsal at `4c349688` copied the reference
LiteLLM model, temperature, output limit and 120-second request timeout
into a reduced legacy fixture. This fixture omitted the unqualified
`drop_params` switch, alternate provider Actions and unrelated reference
Actions. Its source digest was
`sha256:0180b26f65d193cf37f121f48179734b2499f34d568cea5d621fb40c436767f0`.
The converter retired the LiteLLM Action, and a fresh PostgreSQL
bootstrap saved the expected native settings, including `timeout: 120`.
An initial sign-in failed because the fixture encoded its CORS origin
as an array. After using the original fixture's scalar origin, the
browser signed in and completed an actual GPT-4.1 turn.

jvchat displayed "Converted provider verified." and one attempt with no
tool events. Graph Interaction `n.Interaction.c37c1cfae96d4ce393c46e9d`
was delivered in 2.767 seconds, with one request, 249 provider-reported
tokens, USD 0.000534 estimated cost and zero unknown-cost calls. This
qualifies fresh bootstrap and ordinary execution for the reduced
candidate. The request completed well before its deadline, so timeout
enforcement under delayed provider response is still untested.
The full reference app remains blocked.


The offline provider converter accepts a selected service's explicit
model, temperature and output limit when they exactly match the
execution declaration. A finite positive service timeout maps to
the SDK request timeout (`harness_migrate.py:294`). The converter
keeps custom transport switches such as `drop_params` blocked.
This mapping still needs a deployment rehearsal for effective timeout
behavior under the native provider's connection and run limits.

The reference recheck at parent `110ffe6b` produced no candidate.
The report now identifies LiteLLM as selected and lists LiteLLM,
Ollama and OpenAI as declared services without exposing settings or
credentials. Both the script and model-service blockers remain.
Regression cases cover matching settings, conflicting defaults,
invalid timeouts and the explicit transport switch. This round
changes offline conversion only.


The offline converter now retires the legacy model settings for Reply
composition and MCP natural-language selection from a native candidate
(`harness_migrate.py:625`). Its printable report records only Action
and setting names. The candidate preserves Reply channel formatting,
MCP server settings and Action enablement. Independent legacy callers
still require review before cutover.

The reference recheck at parent `2b6171bb` retained source digest
`sha256:e70e627bc5fd6ddbcbbc38644dc39952692e5dac416b0fe8f2e206814dd57162`.
It identified two MCP and three Reply model settings for retirement.
The reference still produced no candidate because its script policy
and model-service declarations remain unqualified. The selected
LiteLLM declaration includes model, temperature, output ceiling,
timeout and drop-parameter settings. The separately enabled OpenAI
and Ollama declarations require caller review. This round changed
offline candidate generation only.


At parent `924cb4de`, the legacy natural-language MCP selector
refuses native ownership before discovery or model resolution
(`mcp_action.py:656`). An isolated fixture returned the existing
structured gateway error in 26 ms with no provider or tool request.
In jvchat on the same disposable PostgreSQL app, the SDK ran the
reviewed `mcp_records__lookup_record` tool exactly once and returned
Cedar at 14:30. Interaction `n.Interaction.014affd6929a49f08f6a3aba`
was delivered in 4.447 seconds. It recorded two GPT-4.1 requests, 793
provider tokens, USD 0.001880 estimated cost and zero unknown-cost calls.
The tested MCP Action blob was `37650319d02ef2302c9fd14b5b3fb8854f0bb084`.

The earlier audit called the legacy selector "sampling". It is a
separate model call that selects a tool from inventory. Server-initiated
MCP sampling is a distinct SDK capability and was outside this fixture.

At parent `97ac197d`, Reply composition now checks native ownership
before shaping or resolving a legacy model (`reply_action.py:546`).
The public literal publish path remains the native response-bus boundary.
An isolated browser fixture called direct Reply composition and received
HTTP 422 in 24 ms with zero provider requests. On the same final backend,
jvchat delivered the exact text "Reply path verified." from GPT-4.1.
PostgreSQL Interaction `n.Interaction.7c5e36679c374837bc2c1ec0` was
delivered in 2.029 seconds with one request, 284 provider tokens,
USD 0.000604 estimated cost, zero unknown-cost calls and no tool events.
The tested Reply blob was `184bc616566fe423c02c3040d7e540201a1d26d9`.
The regression verifies native refusal before compose model lookup.
Reference migration still needs to classify independent Reply callers
and decide how to handle their legacy shaping declarations.

At parent `fb0ff665`, direct Action tree search now requires explicit
accounted completion on a native-owned agent
(`pageindex_action.py:381`). The browser fixture called the same Action
with and without the native adapter. The unaccounted call returned HTTP
422 in 33 ms with no provider request. The accounted call returned HTTP
200 in 2.393 seconds and ranked the renamed PDF section containing
ORBIT-42. PostgreSQL Interaction `n.Interaction.124ff642978b42e490853968`
recorded one GPT-4.1 request, 367 provider tokens, USD 0.001298 estimated
cost and zero unknown-cost requests. There were no chat tool events.
The tested Action blob was `01d265eebbaa46f8cdc627834996902595d4dd43`.
This fixture used the production Action and native search adapter through
a disposable test route. The reference converter still needs separate
qualification for Reply composition, active MCP sampling and the
configured provider declarations.


At parent `c122da99`, the native-owner Action assimilation guard
(`pageindex_action.py:560`) closes a remaining fallback route. Native
agents use authenticated upload or configured Drive sync for ingestion.
The isolated browser fixture called the public Action method on the
native PageIndex read profile. HTTP 422 in 29 ms explained the admitted
ingestion paths. No matching root or Interaction was present in graph
readback and the backend made zero model requests. There were no tool
events, tokens or cost. Tested Action blob
`f756c5846b830a926455f2d847ce2aaa8d963bc5`. The regression checks that the
refusal precedes legacy model resolution. The reference converter still
has model-service blockers, including the separate Reply composition and
MCP sampling contracts.

Source revision `50b6c737`. This is an offline compatibility audit. The
reference agent selects GPT-4.1 through LiteLLM for orchestration and uses
GPT-4.1 Nano through OpenAI for PageIndex tree search and Reply. MCP is disabled
in this declaration and still carries an OpenAI tool-selection configuration.
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
| MCP tool selection | Legacy `fulfill()` resolves a model Action after tool discovery. Native reviewed toolsets use the public SDK lifecycle | Keep reviewed stdio tool selection in the owning native run. Qualify server-initiated sampling separately before admitting a sampling profile |
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
