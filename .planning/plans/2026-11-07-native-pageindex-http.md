# Native administrative PageIndex tree search

The existing admin-only PageIndex search endpoint now uses the enabled native
harness configuration for tree ranking when the agent opts into tree search
through `PageIndexReadAction`. A native owner without that profile rejects tree
search before retrieval. Existing direct and walker routes and agents with a
legacy owner keep their existing dispatch. The HTTP route still searches the
collection identified by the path's agent ID and returns its existing results
shape. The chat tool keeps its Action-configured collection.

Each admitted operation has a graph Conversation and Interaction attached to
the harness Action outside the user's chat history. The server establishes the
operator from the authenticated request. The graph holds typed SDK run
records, usage and spend receipts, final result, and operation outcome. Public
Pydantic AI Agent runs rank the bounded tree projections using the configured
provider. The runs share a usage object, limits, an operation deadline, and the
operation's spend journal. Subsequent ranking runs retain lineage to the first
SDK run. No separate execution storage is introduced. The operation uses the
existing local worker restriction. Its channel name is
`pageindex_admin_search` for declarative limits.

## Browser qualification

The [browser and graph observations](2026-11-07-native-pageindex-http-smoke.json)
identify the disposable PostgreSQL app, isolated PageIndex JSON fixture, backend
source and frozen jvchat artifact. From an authenticated browser session, the
real GPT-4.1 HTTP lookup returned the Harbor handbook's Dock Room 4,
HARBOR-41 and stored source URL in 3.9 seconds. The other-collection lookup
returned an empty result in 83 ms and created no provider request. An
unauthenticated browser request received a backend 401 before operation
admission. The browser presented that rejection as a fetch error.

A jvchat replay on the same build completed skill loading and PageIndex search
and returned the seeded Willow room and booking code. Across HTTP and chat
there were six provider requests, 4,749 reported tokens and USD 0.009036
estimated cost, reconciled with the graph. Reload preserved the compared graph
records exactly. The initial HTTP source used an unsupported hook constructor
and failed twice before provider dispatch. Those receipts remain in the evidence.
The full repository test gate also rejected a direct sibling Action import.
The endpoint now resolves the profile through the agent graph and checks its
PageIndex base contract. The final browser replay returned the same authorized
record in 4.4 seconds.

Focused regressions verify independent operator scope, shared request limits
across multiple ranks, SDK lineage, and refusal before retrieval when native
admission or authenticated actor identity is missing.

## Remaining retirement gates

These administrative receipts have per-operation budgets. Cross-operation
quotas, retention and operator recovery for interrupted administrative
operations remain separate deployment gates. Ingestion, Drive sync and
jvforge callbacks still require native qualification. The reference converter
continues to block model-service retirement. See the
[model service audit](2026-11-07-native-model-service-boundary.md).

## Separate administrative search model

The enabled PageIndex read profile may set `native_search_model`.
Administrative tree search forwards that host choice to the existing
accounted operation. Empty uses the chat model. Direct search does
not resolve a provider. The override shares the native owner's
settings, limits and deadline. Sources: `pageindex_action.py:116`,
`endpoints.py:1998`, `native_search.py:43`.

At parent `6ff4b67c`, a loopback browser fixture sent an authenticated
request to the production endpoint on a fresh disposable PostgreSQL
app and JSON PageIndex. Chat was configured for GPT-4.1 and the search
profile for GPT-4.1 Mini. The browser displayed HTTP 200 in 2.714 seconds,
Dock Room 4, HARBOR-41 and the stored citation. The separate collection
canary was absent. Graph Interaction `n.Interaction.219d75ad37a041cb87f24b88`
completed with the Mini model in the SDK journal, one call, 262 provider
tokens, USD 0.000204 estimated cost and zero unknown-cost events. The
direct control returned the same hit in 20 ms without creating a new
Interaction or provider attempt. Neither request emitted chat tool events.
In-run ranking and ingestion model selection remain separate work.

Runtime diff SHA-256: `b90700528fcc43e2d96b3ff1a5b1c95a998ea27ce6c07f6f36e2f3bce29e579a`.
