# Next dependency: shared host extraction before interview execution

Status: native local admission/transport extraction implemented; see
[results and remaining gates](2026-11-05-shared-host-results.md).
The original call-graph evidence below describes the pre-extraction parent.
Governing [execution contract](2026-11-04-harness-rebuild-execution.md).
The [live probe](2026-11-05-interview-host-smoke.json) distinguishes cold-import
isolation from a real HTTP turn. The latter still loads legacy HarnessRuntime.

## Evidence and ownership boundary

- Interview engine imports the shared visitor context directly; importing its
  metadata no longer loads the old orchestrator loop/executor
  (`jvagent/action/interview/engine.py:15`, `orchestrator/__init__.py:29`). The
  legacy archetype export still loads its endpoints when explicitly requested.
- Shared ingress creates legacy identity mirrors, a correlation, spans and a
  session lease before applying entry access, and binds that lease after creating
  an Interaction (`interact/interact_walker.py:358`, `:490`).
- ResponseBus appends every response/tool event to the legacy runtime outbox;
  cursor streaming reads it (`response/response_bus.py:413`, `streaming.py:107`).
- Embedded cancellation/admission also calls the legacy runtime
  (`embed/interact.py:74`, `:532`). These callers are part of cutover, not optional
  omissions after HTTP browser success.
- Signed host-context verification is a reusable authenticated host service,
  currently under the old orchestrator namespace (`orchestrator/host_context.py:194`).
  Conversation mutation locking is already a separate JV service and wraps walker
  execution (`interact/interact_walker.py:558`). Preserve its protection.

The native Agent already owns model/tool/skill execution. The shared legacy
admission/outbox state must not become a permanent second harness underneath it.
Do not merely suppress an import or discard security checks to turn the probe green.

## Execution sequence

1. Freeze the real HTTP/embed call graph. Add process-level acceptance that records
   imports after startup, caller admission, paid tool execution, publication and
   cancellation. Cold class-import assertions alone are insufficient.
2. Extract host-only identity/context responsibilities into core/ or tooling/:
   graph-established caller/session, opaque server run/correlation identity,
   signed host-context verification and bounded graph nonce consumption. Preserve
   existing public import contracts with forwarding aliases until migration.
   Do not copy the legacy identity dictionaries, run state machine or generic
   invocation/checkpoint engine.
3. Bind native admission explicitly from validated configuration. Keep native run
   identity consistent with verified host context and SDK run identity. Preserve
   entry AccessControl and the conversation mutation lock; consume replay-sensitive
   context under the same ownership boundary. Refuse mixed execution owners and
   unsupported distributed ownership settings visibly. Never route native errors
   back through the legacy engine.
4. Remove native ingress/embed and publication dependencies on get_runtime. Native
   execution state stays in the official graph-backed stores. ResponseBus retains
   its transport responsibilities. Preserve literal publication, scoped fast-reply
   replay, stable message IDs and uncertain-delivery state. Legacy cursor semantics
   cannot silently masquerade as native durable recovery; provide an explicit
   unsupported outcome until graph-backed delivery replay is qualified.
5. Reconcile cancellation, unknown usage and delivery through public library
   persistence interfaces and JV graph records. Do not introduce another state DB,
   lease engine, tool scheduler, model protocol or recovery loop. Keep any genuinely
   necessary upstream-gap adapter small and give it a removal condition.
6. Only after these boundaries pass, enable interview domain adapters: authored SOP
   inheritance, explicit activation, validator/session storage, continuation and
   reviewed hooks. The existing task-lock/use_skill directives must be migrated
   deliberately; do not strip those fields and pretend their behavior survived.

## Acceptance and removal gates

- An installed InterviewAction can coexist with a real native model/tool turn;
  the orchestrator loop, pilot runtime, old executor and generic HarnessRuntime
  are absent throughout that turn and response finalization.
- Invalid/foreign/replayed/expired host envelopes, changed caller scope, denied
  access, concurrent turns and configured lease loss fail with defined outcomes.
- HTTP streaming, popup messenger and embedded entrypoints use the same host
  identity and native execution ownership; no silent legacy fallback.
- Cancellation before/during model/tool/publication preserves known receipts and
  unknown outcomes. Recovery does not repeat uncertain external effects.
- Existing modular extension and explicitly requested legacy export regressions
  remain green until their migration/deletion step. Then delete their obsolete
  implementation and compatibility aliases with a caller/test/config ledger.
- Run actual-provider browser success and adversarial failure cases on disposable
  graphs; record source revision, UI/events, graph/task/result state, latency and
  estimated/actual/unknown usage. No enterprise claim from import isolation alone.

Ordinary-member browser isolation, distributed ownership, durable egress replay,
interview execution, migration and legacy deletion remain open.
