# Native SDK media persistence

InteractionStepStore externalizes SDK message payloads through the public
`externalize_media` function before writing new snapshots and restores them through
public `restore_media` before returning typed snapshots. `jv_media_version: 1`
marks the storage encoding; snapshots without this marker retain their old inline
interpretation and are not rewritten. Unknown versions refuse. The official SDK
owns the media marker grammar and literal-key collision escaping; JV does not add
a second message serializer or replay engine. Sources:
`jvagent/harness/step_store.py:59`, `:187`.

Payloads of at least 64 KiB move to a declared [NativeMedia](../GLOSSARY.md#native-media) graph node. Images/PDFs
use public BinaryContent; UTF-8 attachments use public TextContent so the library
can externalize their contents too. Bare legacy strings inside content lists remain
inline. Source: `jvagent/harness/inputs.py:55` and `:108`.

GraphMediaStore implements the official MediaStore protocol on the existing
jvspatial node/edge collections, across the application's configured graph backend.
There is no extra database, raw download route or file resolver. Each payload is
stored once per agent/user/session/conversation scope, with a deterministic scoped
node identity and the official content URI. Public `create_if_absent` makes initial
creation atomic and never overwrites a stored winner. Reads use authoritative
queries, validate the persisted SDK host binding and conversation owner, and check
byte bounds, decoded size and content hash. No model-selected storage scope is
accepted. Sources: `jvagent/harness/media_store.py:31`, `:92`, `:121`,
`jvagent/memory/native_media.py:9`.

Payload size is limited to 10 MiB per media node, with at most 8 KiB of string
metadata. Node payloads use base64 because the graph persists JSON-shaped fields;
backend record limits still apply to the complete entity. This is
checkpoint deduplication, not a claim that binary storage has no encoding overhead.
No overall database or throughput optimization is claimed from serialized sizes.

The owning Conversation connects directly to its media nodes. Conversation
cascade deletion removes its dependent media; Interaction pruning leaves media
available to later SDK snapshots. Automatic reclamation of blobs no longer
referenced after compaction, aggregate capacity, partial-write orphan reconciliation
and distributed ownership remain separate qualification work. Do not delete media
by age while any retained snapshot might reference it. Source:
`jvagent/harness/media_store.py:152` and the conversation deletion contract at
`jvagent/memory/conversation.py:1311`.

Missing/corrupted media refuses history reuse before a new model request. It uses
the existing qualified recovery-notice chain rather than dropping the affected
content. An operator may restore the exact original bytes through the public
GraphMediaStore adapter, which revalidates scope and hash. This is manual data
restoration, not a model-triggered repair operation or replay approval. The local
smoke demonstrates a fresh user turn after restoration without changing original
Interaction records. Sources: `jvagent/harness/recovery.py:117`,
`jvagent/harness/media_store.py:95`.

The scoped [qualification record](../plans/2026-11-06-native-media.md) covers local
OpenAI browser, JSON and PostgreSQL cases. Other deployments, physical database
size/performance, maximum-size/hostile documents, automatic garbage collection,
raw pre-version history migration and full enterprise release remain unqualified.
