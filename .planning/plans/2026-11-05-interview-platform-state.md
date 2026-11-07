# Interview compatibility prerequisite: preserve platform replay state

Parent `d664e4db`; branch `feat/pydantic-harness-rebuild`; author `eldonm`.
All nine parent CI checks passed. Governing [execution plan](2026-11-04-harness-rebuild-execution.md),
[graph/source receipts](2026-11-05-interview-platform-state-smoke.json), and
[browser protocol/UI evidence](2026-11-05-interview-platform-state-browser.json).

## Confirmed defect and repair

Interview cleanup could delete the consumed-host-nonce ledger from Conversation
context. The same still-unexpired signed request was then accepted again. The
reproduction calls actual native admission, the real domain cleanup function and
admission again, with batching on/off: both cases fail on the parent because replay
returns ready. This violates host replay protection even though the interview
mutation driver itself is not yet qualified for native execution.

Core now publishes the canonical replay-state key and retains its private alias
for existing host imports. Interview teardown treats that key as platform state,
independent of per-skill retain lists. It still removes interview and application
scratch. The protocol, key storage, bounded ledger, expiry, lock, strict flush and
SDK execution path are unchanged. No generic context framework or state DB was
added (`jvagent/core/host_context.py:20`, `jvagent/action/interview/session.py:109`). The subsystem
AGENTS contract now makes this retention requirement explicit.

## Actual browser evidence

The disposable authenticated host signs a fixed policy for a fresh server-owned
session. jvchat sends it to the public embedded entrypoint; GPT-4.1 returns
INTERVIEW-PLATFORM-READY, a value supplied only by that policy. The existing
InterviewAction is installed, while native interview activation remains gated.

An administrator-only fixture invokes real interview scratch cleanup under the
conversation lock. The fixture reports one nonce entry before and after, with
interview/scratch removed. Resending the exact envelope while it is still unexpired
returns invalid_host_context and a visible safe error. No replay Interaction or
provider call is created. Fresh SQL has one delivered Interaction and the retained
nonce entry. Browser preconditions include envelope expiry and both unexpired
submission times, rather than accidentally rejecting an expired token.

The valid model request used 302 provider-reported tokens, USD 0.000658 estimated,
and 3.546 seconds in the Interaction. Provider-invoiced cost is unavailable.
Browser timings and the 25 ms cleanup request are in the receipt. Public responses
contain no host envelope or signature; signing keys, signatures and nonce values
are omitted. The temporary fixture source is retained for reproduction and is not
a shipped API. No provider/backend execution function was patched.

## Validation and a fault-test correction

Affected interview/native-host/legacy-host tests: 308 passed (2.33 s). Two new
real-PG cases prove retained replay state with batching on/off using fresh database
reads. Combined native cohorts passed on Python 3.11 and 3.12: 238 each (32.00 s
and 35.82 s respectively, 155 warnings each). All-file hooks passed with staged evidence included. New-head CI and the normal
full-suite push hook remain independent gates.

The first combined run also found an unsound assertion in a separate PostgreSQL
timeout test: it assumed no record could appear after releasing the lock following
a client timeout. A timed-out write was observed in fresh SQL after unlock. The
test now retries while the exclusive lock is still held and requires a second
timeout, then reads under the owning lock to prove no acknowledgement could have
been justified. After unlock, explicit retry must acknowledge exactly one durable
record. This strengthens the false-acknowledgement check without assuming rollback.
All sixteen PostgreSQL cases passed afterward (10.55 s). No adapter implementation
was changed to hide the observation. Initial failure remains part of this record.

## Course for the native interview adapter

The dependency audit identified these required steps before claiming compatibility:

1. Preserve platform state across domain teardown. This increment closes the
   confirmed replay hole. Historical lost ledgers cannot be reconstructed; cutover
   must retire old in-flight envelopes rather than invent consumed nonces.
2. Preserve authored SOP inheritance and additive tool declarations through a
   read-only asset projection. Reuse JV declaration facts, but fail closed on
   missing/cyclic bases rather than copying permissive discovery fallbacks. Official
   Skills must still own catalog/load behavior. Its public workspace backend is
   the intended boundary, not a second skill interpreter. See [official Skills docs](https://pydantic.dev/docs/ai/harness/skills/).
3. Give activation an explicit domain-tool boundary with SDK effect persistence.
   Do not call the old use_skill router or auto-store user text during startup.
   Version the Action-owned native base SOP and report deprecated custom directives
   requiring migration instead of fuzzy Markdown rewriting.
4. Classify and qualify built-in graph mutations before lifting the current native
   read-only gate. Preserve field validation, branching, review/confirmation,
   cancellation/reset and durable session state; use official tool execution and
   effect records. Do not label mutation handlers as reads to make them pass.
5. Separate domain task/session records from old runtime turn-lock machinery.
   Qualify a selected interview workflow through activation, collection, review,
   completion and backend restart, then qualify multi-skill detours/continuation.
   Keep caller/tool permissions independent of SOP instructions.
6. Qualify trusted extension validators/hooks and custom tools by their effects.
   Do not grant scripts ambient shell/filesystem privileges or permit unknown
   external effects through a generic interview wrapper. Exercise failure and
   cancellation before/after writes; unknown outcomes require reconciliation.
7. Only after those workflow and compatibility gates pass, migrate declarations
   and delete superseded use_skill/continuation/turn-lock execution responsibilities
   under the main deletion ledger. Retain tested domain contracts, not old engines.

This increment is a required security prerequisite, not a substitute for that
vertical adapter. Native admission still rejects unqualified extends/task-lock
semantics and undeclared/mutating tools. Full interview/flow/custom-hook execution,
operator recovery, migration, legacy removal and enterprise qualification remain
unfinished.
