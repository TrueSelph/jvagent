# Reference model service retirement boundary

## Explicit PageIndex configuration conversion

`harness-migrate --pageindex-model openai:gpt-4.1-mini` converts the
reviewed legacy OpenAI tree declaration into a native read profile and
sets the search, ingestion and callback models
(`harness_migrate.py:406`). The old service selector and model value are
removed from that profile. A selected skill that declares an unavailable
PageIndex tool blocks conversion (`harness_migration_skills.py:198`).
Unreviewed Action settings also block. Operators must separately enable
and qualify ingestion and callback routes and verify retained bindings.

On the script-qualified full reference copy at parent `2b174fa0`, the
offline dry run accepted the PageIndex mapping and left Intro and SkillHub
as unreviewed Actions. Alternate OpenAI and Ollama declarations still
prevent a full candidate. The regression uses a reduced declaration to
verify candidate output, settings preservation and source immutability.
This increment changes offline planning only.

## Reference PageIndex provider mismatch

At `1862a530`, the reference config names GPT-4.1 Nano for PageIndex
and GPT-4.1 for chat (`agent.yaml:337`, `:445`). The existing native
HTTP routes require an enabled PageIndexReadAction and resolve the
chat owner as the operation provider (`endpoints.py:1970`, `:2170`;
`native_search.py:101`). In-run ranking inherits its parent's model
and settings (`completion_tool.py:37`).

An offline probe passed the model name from the reference declaration
to the production native callback with the reference chat model.
It returned `Requested model is not enabled for PageIndex completion`
before starting an operation (`native_callback.py:24`).
The callback regression verifies that a rejected model creates no
additional graph receipt or provider call. Copying the legacy model
string onto PageIndexReadAction would therefore leave two gaps:
implicit use of the chat model for ranking and explicit rejection
of the reference callback model.

Preserve the model choice with an explicit host-owned PageIndex model
policy before migrating this Action. Use that policy for callback,
HTTP search, ingest and authorized in-run ranking through the current
SDK completion adapters. Bind settings and spend receipts to the
actual selected model. Keep request payloads as validation of the
host choice and keep the existing limits, collection scope and
accounting. Rehearse the GPT-4.1 chat and Nano PageIndex split on a
disposable graph before replacing the legacy declaration. The probe
changed no runtime code and made no provider or browser requests.

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

## Native first-user presentation

The converter folds the reviewed Intro Action into literal
`first_user_instructions` on the native owner. It preserves disabled
behavior and refuses custom Action scheduling. The native host selects
the instruction from the walker's server-resolved new-user flag and
passes it to SDK instructions for that run. Sources:
`harness_migrate.py:406`, `instructions.py:6`,
`native_harness_action.py:520`. The reference dry run now reports SkillHub
as the remaining unreviewed Action alongside the alternate providers.

At parent `f2560463`, a loopback browser fixture exercised production
streaming ingress with actual GPT-4.1. The first fixture lacked a required
skill and returned an admission notice in 212 ms without a model call.
A fresh disposable PostgreSQL graph with a minimal readiness skill then
showed `HARBOR-WELCOME` and `READY.` in 1.943 seconds. Interaction
`n.Interaction.3c099ab80f5040f086f06d7d` was delivered with one request,
260 provider tokens and USD 0.000580 estimated. The same authenticated
user started a distinct conversation and saw only `READY.` in 2.361
seconds after one capability call/result. Interaction
`n.Interaction.f3d95b9aeb304d8383ca56cd` was delivered with two requests,
539 provider tokens and USD 0.001204 estimated. Both recorded zero
unknown-cost events. The browser test used a streaming fixture without
jvchat. Retained-graph migration remains separate.

Runtime diff SHA-256: `a2b9cd591a7b68101d5c13531605c24693697988099ff69cc217cbb7150d9cde`.

## Operator-managed skill catalog conversion

`--operator-managed-skills` explicitly retires the reviewed legacy
SkillHub declaration in the generated candidate. Operators manage
the existing skill assets and the native owner's explicit catalog
through app configuration. No asset is rewritten. A selected skill
that names SkillHub or matches one of its tool names prevents the
conversion. Unknown SkillHub settings also block.
Sources: `harness_migrate.py:406`, `harness_migration_skills.py:220`.

At parent `0d82dafa`, the offline reference dry run combined the
script-qualified source, the explicit PageIndex model and the
operator-managed catalog. It reported no unreviewed Actions. LiteLLM
remains the selected legacy provider, and the additional OpenAI and
Ollama declarations still block a full candidate. Regression fixtures
confirm candidate creation with a single eligible model service,
refusal without opt-in, and refusal when a selected skill depends on
a SkillHub tool. This increment changes offline planning only.

A second disposable copy removed the two alternate provider declarations.
The planner then isolated the unsupported selected-service setting.
Removing the legacy LiteLLM `drop_params` switch from that copy yielded
a full configuration candidate, written mode 0600 by the public CLI.
Source digest
`sha256:3f6ce7cda9f1a38d0a48fb99ebf52583741b762db48c3680b8d3e5c4dca6bee3`.
The tracked source and graph were untouched. This candidate has not
been bootstrapped or exercised in a browser. Retained-graph cutover
and provider retirement approval remain separate.

## Fresh reference candidate bootstrap and browser smoke

At `43f78f67`, the generated candidate with SHA-256
`e5eb2e641fde4d0a3113042ab50abcd2e787d2d8c4962490890e0f17bf74ebb7`
booted on a fresh disposable PostgreSQL graph, with the reference app
configuration restricted to its migrated Orchestrator agent. It used
a bounded 48 MiB volume and the reviewed combined script image. Graph
readback found the native owner and no legacy model or Orchestrator
owner nodes. The loopback browser fixture submitted production streaming
requests under the disposable account using actual GPT-4.1.

| Browser case | Result | PostgreSQL Interaction | Usage |
|---|---|---|---|
| Plain chat on the generated candidate | HTTP 200 in 2.406 s, REFERENCE-READY and an introduction, no tool events | `n.Interaction.1d49245897784d048b2d9447`, delivered | 1 call, 1,422 provider tokens, USD 0.003054 estimated |
| Triage on the generated candidate | HTTP 200 in 2.983 s with skill load, shell attempt and permission refusal | `n.Interaction.78fe861b4e5c477aa4a1a13b`, failed | 2 calls, 3,233 provider tokens, USD 0.007090 estimated |
| Triage after adding an explicit disposable shell grant and restarting | HTTP 200 in 24.831 s with skill load, shell result and correctly ranked incidents | `n.Interaction.2b3ef91a0418482e8cddec16`, delivered | 3 calls, 5,191 provider tokens, USD 0.008732 estimated |

All three Interactions recorded zero unknown-cost events. The sorted
`ranked.json` existed in the expected user sandbox. Docker readback
verified no network and a read-only root. The initial bootstrap
reported ElevenLabs dependency installation errors because the native
test interpreter lacked pip. Preinstalling the declared dependency
into the disposable dependency path cleared those errors on restart.
The app had no live channel credentials. The browser fixture displayed
the production stream without jvchat. The separate LeadGen agent,
live channel providers, remaining reference skills and retained graph
conversion were outside this smoke.

## Separate LeadGen reference declaration

The fresh reference rehearsal above exercised the Orchestrator agent.
The second agent in the app descriptor, `jvagent/leadgen_agent`, was
rechecked at parent `5783e2b6` against source digest
`sha256:fc12603075e4667c39d4a4a804ee15c4a0d00288f6a61c0f2a4a717e200dd4b3`.
Its offline plan still has five blocker entries.

- The enabled user-scoped filesystem MCP has no reviewed native read
  list. Its lead synchronization requires a separately qualified
  mutation path, including effect ownership and receipts
  (`leadgen_agent/agent.yaml:141`).
- The selected `product_inquiry_leads` and `example_leadgen` assets
  use underscore names and extend the LeadGen Action. Canonical names
  alone would leave their Action extension, hook and sync semantics
  unresolved (`leadgen_agent/agent.yaml:32` and each skill frontmatter).
- Vision still selects an independent legacy model
  (`leadgen_agent/agent.yaml:187`). Suggestions independently resolves
  a model after streaming replies
  (`suggestions_interact_action.py:266`).
- PageContext currently adds response parameters from visitor page data
  (`page_context_interact_action.py:100`). Its native presentation
  mapping needs a trusted-data review. UI rendering, LeadGen and the
  app-local Storefront still require declaration-specific review.

Deepgram STT uses its own SDK and credentials
(`deepgram.py:57`, `:93`, `:149`). The offline converter can preserve
that declaration when retiring a qualified chat service. The regression
asserts the declaration survives unchanged. The reference report now
lists six unreviewed Actions plus the alternate OpenAI declaration.
No LeadGen candidate was generated and no runtime behavior changed.

### LeadGen skill Action-base admission

At parent `c8a4ca8b`, both selected reference skills still failed
`project_skill` after their names and directories were normalized
in disposable copies. The projector refused the missing reviewed
Action base. The CLI now distinguishes that dependency from a generic
format failure and continues to refuse the candidate. The canonical
regression exercises the same refusal without editing tracked assets
(`harness_migration_skills.py:260`).

The legacy base advertises capture, retrieve, status and sync
(`jvagent/action/leadgen/SKILL.md:8`). Only status has a reviewed
native read contract (`tools.py:129`). Retrieve uses
`get_or_create_for_user` (`engine.py:397`), so it needs a true read
path before it can be exposed as one. Capture can run hooks, persist
profile changes and initiate auto-sync (`engine.py:271`, `:181`);
admit those effects only with explicit host permission and SDK
receipts. The native base should describe only the operations that
its owning Action can safely supply. This was offline qualification,
with no graph writes or model requests.

### Native LeadGen profile retrieval

Native LeadGen exposes status and a separately permitted retrieval tool.
Retrieval uses a detached profile view for missing or changed required
fields and does not create or update a graph row. The visitor binding
supplies caller identity and the model-facing schema accepts no extra
arguments. Legacy retrieval retains its existing write behavior.
Sources: `leadgen_action.py:101`, `engine.py:392`.

At parent `425e348c`, a browser fixture used the production streaming
endpoint, actual GPT-4.1 and a disposable PostgreSQL app. A first attempt
used the persisted readiness configuration because of merge precedence
and did not call LeadGen. It took 3.510 seconds and recorded two requests,
588 provider tokens and USD 0.001476 estimated. After applying the
LeadGen config and named permission and restarting, the browser showed
`leadgen__retrieve`, an empty profile with missing email, and a reply
confirming no email was on file. HTTP 200 took 3.190 seconds.
Interaction `n.Interaction.3ac7b4506d324584bda7f062` was delivered with
two requests, 974 provider tokens and USD 0.002254 estimated. Neither
attempt had unknown-cost events. PostgreSQL contained no LeadProfile.
The model offered to update the profile despite the read-only skill.
No write tool was available or called. Capture, sync and the native
Action skill base remain open.

Runtime diff SHA-256: `3d423909fc8e9af03dda441b34c97949a5b51dac75620304a74f9c291fd52a1f`.
