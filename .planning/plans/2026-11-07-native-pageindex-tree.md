# Native PageIndex tree ranking through an Action tool

The existing `jvagent/pageindex_read` Action can opt into `strategy: tree_search`
for native skill execution. Its default stays direct. The existing
`pageindex__search` name, PageIndex skill assets, graph queries and result format
carry forward. Native admission closes the tool schema and pins the strategy
from trusted Action configuration. The host binds the ranking callback after
skill ownership and permission checks. See `jvagent/action/pageindex_read/pageindex_read_action.py`
and `jvagent/harness/native.py`.

The callback runs a tool-free public Pydantic AI Agent using the parent's active
provider, settings, deadline, shared SDK usage and limits. It passes only the
ranking prompt to that Agent. Official StepPersistence and SpendLimits create
child lineage and record requests through the same graph adapters. The host
restores the interaction graph scope for child receipts because PageIndex queries
run in a separate graph context. The tool is sequential so sibling tool calls
cannot race its budget admission. As with the existing host policy, settlement
can detect a provider response that crosses the cost ceiling after billing.

Native ranking bounds the local tree projection by its UTF-8 byte length and
uses SDK provider accounting for measured token usage. It selects only IDs that
were in the projected tree. PageIndex retains its existing lexical retrieval
when the projected tree is too large or yields no results. A native provider
failure propagates to the host, with no legacy provider retry or lexical success
substitution. See `jvagent/action/pageindex/retrieval.py`.

## Evidence

The [browser and graph observations](2026-11-07-native-pageindex-tree-smoke.json)
record the initial integration failure, the corrected provider runs and the
final formatted source smoke. Actual OpenAI GPT-4.1 was used through jvchat in
an isolated app and PostgreSQL graph. PageIndex used an isolated JSON index
with a public synthetic handbook and a canary document in a separate collection.
The first run exposed LiteLLM in the legacy token counter. The native byte
preflight removed that dependency. The backend source and frozen frontend
identities are recorded separately.

The focused regressions cover shared request and cost limits, provider failure,
absence of legacy counter/transport calls, graph lineage, and strategy/schema
pinning. A separate collection query returned no match and made no ranking
request. The authorized query returned the expected room and booking code.
The source URL was persisted in the tool result and rendered as a clickable link
when explicitly requested. The initial successful answer cited the handbook by
name without linking it. Browser reload readback preserved interactions,
conversations and run registrations exactly. The attempted two-request browser
fixture used the existing 20-request value because merge sync preserved the
persisted setting. Its normal success is recorded under the effective policy.
The deterministic SDK tests cover request and dollar refusal.

On the final source, 944 tests passed in the Python 3.11 native integration
lane, including real PostgreSQL and reviewed Docker fixtures. The Python 3.12
focused completion, delegation and PageIndex suite passed 26 tests. Both full
native lanes passed 944 before extracting the callback into its adapter.

## Migration gates

The standalone administrative HTTP tree-search endpoint, full PageIndex Action,
ingestion, Drive sync and jvforge callback still need bounded native execution.
This increment is limited to the host-admitted tool path. The reference converter
continues to block retirement of the model services. See the
[model service audit](2026-11-07-native-model-service-boundary.md) for the remaining
entrypoints and retirement criteria.
