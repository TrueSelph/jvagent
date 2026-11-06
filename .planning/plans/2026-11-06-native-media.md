# Official SDK media references on the object-spatial graph

## Implementation and ownership

InteractionStepStore now uses public Harness `externalize_media` and
`restore_media` functions around its typed snapshot storage. Payloads at or above
64 KiB move into conversation-owned NativeMedia nodes in the same jvspatial
node/edge collections. A versioned storage envelope distinguishes new references
from existing inline snapshots, which remain readable without rewriting. Literal
marker collisions are escaped/restored by the library. JV does not introduce a
second message parser, replay engine, state database or file-download route.
Sources: `jvagent/harness/step_store.py:59`, `:187`.

The public MediaStore adapter validates stable host/SDK/conversation ownership,
scopes deterministic content identities to agent/user/session/conversation, and
checks decoded byte count/hash. Public jvspatial `create_if_absent` prevents
replacement of a stored winner. Authoritative queries prevent a warm entity cache
from masking physical removal. Payload bytes and metadata are bounded; payloads
are omitted from Python object repr. Conversation edges give normal cascade
ownership; Interaction pruning retains bytes needed by later snapshots. Sources:
`jvagent/harness/media_store.py:31`, `:92`, `:121`,
`jvagent/memory/native_media.py:9`. See the
[storage and recovery contract](../runbooks/native-media.md).

UTF-8 attachments now use public SDK TextContent. The library cannot externalize
bare strings inside content lists without breaking that union's shape; typed
content avoids creating a custom workaround. Old bare strings remain unchanged.
Source: `jvagent/harness/inputs.py:108`.

A missing/corrupted payload refuses history reuse through the existing qualified
recovery-notice chain. Restoring exact bytes through the public graph adapter lets
a fresh user turn use the retained SDK history. It does not replay a model call or
rewrite earlier execution facts. Source: `jvagent/harness/recovery.py:117`.

## Adversarial and persistence evidence

The parent StepStore reproduces two failing regressions: it retains the large
payload inline and does not fail a snapshot write when media persistence fails.
The implemented adapter passes those cases. Added regressions cover scope/agent/
registration changes, corruption/count mismatches, foreign conversations, missing
bytes, immutable/deduplicated identities, metadata, no public URL, conversation
cascade, old inline snapshots, reserved marker literals, unknown versions,
authoritative reads after physical deletion and malformed payload types under
Pydantic fast construction. An SDK-level failure test proves media checkpoint
failure precedes both ordinary and streaming model request methods. Sources:
`tests/harness/native/test_media_store.py:1`.

The first fixture compared BinaryContent and the SDK-normalized BinaryImage by
class equality. It now compares public canonical message serialization, preserving
all values/bytes rather than requiring an unchanged Python subclass. This was a
fixture expectation correction, not a runtime relaxation.

Fresh PostgreSQL connections restore binary/text bytes with deferred saves both
on and off. Final native/host/real-PostgreSQL lanes pass 652 cases on each Python
3.11/3.12. Focused storage/input/recovery checks pass 94 before the five additional
malformed-type cases; the final dedicated media slice passes 27. Sources:
`tests/harness/native/test_postgres_store.py:90`.
Clean native-only wheels pass dependency checks and graph CAS/deduplication, SDK
snapshot restoration and typed-text probes on both Python versions. The initial
probe used a nonexistent JSON-backend close method; its cleanup was corrected
without runtime changes. Staged all-files hooks pass; normal full pre-push remains
a publication gate. The backend-wheel check does not qualify packaged chat/
messenger bundle delivery. No dependency pin or extraction package is added.

## Actual-provider browser and size evidence

Parent 7e31bf19. A fresh disposable PostgreSQL app, backend 8163 and source jvchat
3140 use actual GPT-4.1 with a 274,755-byte PNG and an 80,073-byte UTF-8 note.
Synthetic Files enter through the real composer change handler. Answer values are
absent from the prompts. [Machine evidence](2026-11-06-native-media-smoke.json)
binds per-process source blobs, SDK state digests, decoded hashes, receipts,
request outcomes, durations, storage comparisons and operator/readback audits.
[Browser observations](2026-11-06-native-media-browser.json) retain file previews,
SDK skill-load activity, replies, recovery notice and usage.

1. Initial read returns the image code/color and note header correctly. Three
   snapshots occupy 10,311 encoded bytes versus 1,353,927 for equivalent inline SDK
   snapshots. The comparison uses the same serialization after public restoration;
   the predeclared threshold is at most 20%. Each measured turn remains below it.
2. Cold restart preserves original records/spend. A follow-up recovers the note's
   previously unanswered final reference, but selects the wrong white image label.
   This is a retained partial request failure. A more specific user question reads
   the correct right-hand caption; no fixture-specific runtime steering is added.
3. A privileged operator removes the owned image payload while the backend remains
   live. The next browser turn receives the recovery notice with zero attempts,
   tokens, spend or new SDK registration. No old content is silently dropped.
4. The operator restores exactly the original node through the public adapter;
   the stored winner matches the original raw node. Earlier Interaction records
   remain exact. A fresh user turn reads code/caption/final reference successfully.
5. A further cold restart and actual-model read return all requested values on
   final runtime source. Reload leaves all six Interactions, five registrations
   and spending unchanged. The same two media nodes back all eighteen snapshots;
   no new media is created by re-reading them. Only SDK load_capability is invoked;
   no domain task, Action write or external delivery effect occurs.

Six interactions include five model turns and one expected zero-attempt refusal.
There are six provider requests, 89,841 provider-reported tokens and USD 0.069402
estimated cost, with zero unknown-cost calls. Every receipt reconciles against
conversation accounting. Five scenario outcomes are complete and one is partial.
The size result concerns encoded checkpoint JSON only. It does not establish
physical PostgreSQL disk use, throughput, latency or provider-token improvement.

## Remaining gates

Automatic reclamation after compaction, partial-write orphan reconciliation,
aggregate media quotas, maximum-size/hostile files, binary compaction, backend/
provider/deployment capacity and operator repair UX remain unqualified. Existing
large bare-string content lists remain inline; pre-version graph transformation
is separate migration work. Older software cannot be assumed to understand the
new references; an operator-reviewed lossless re-inline/rollback export is not
implemented. Image-reading accuracy still needs a representative evaluation
cohort; the partial caption result is not hidden by successful data transport.

Local JSON/PostgreSQL adapter and OpenAI PNG/text evidence does not close remote/
channel media, full legacy vision/artifact parity, stored-state cutover, distributed
recovery, security/deployment, evaluation or old-engine deletion. The reference
profile remains at 12 migration blockers and the full goal remains active.
