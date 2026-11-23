# jvchat in-flight request ownership

Parent `42e21125`; branch `feat/pydantic-harness-rebuild`; author `eldonm`.
All nine parent CI checks passed. Governing [execution plan](2026-11-04-harness-rebuild-execution.md),
[preceding reset repair](2026-11-05-jvchat-reset.md),
[final browser evidence](2026-11-05-jvchat-ownership-browser.json) and
[final SQL/build/source receipt](2026-11-05-jvchat-ownership-smoke.json).

## Confirmed defect and repair

The old hook used shared mutable stream flags/controller cleanup. A request retired
by Stop, reset or sidebar selection could still emit chunks/errors, return its
session ID and execute finally against a newer request's state. New Conversation
also did not explicitly abort a first request whose parent session was still
undefined. Three controlled regressions fail on the parent: reset before session
binding, sidebar switch, and Stop followed by another send.

Each send now retains its own AbortController identity. Only that owner may process
chunks/errors, finalize controls or return a session ID. Retirement clears ownership
before aborting. Reset, Stop, sidebar transitions and cleanup retire the request;
initial server binding preserves it. Attachment preparation reserves ownership
before awaiting file reads, and abandoned preparation cannot submit an API request
(`jvchat/src/hooks/useStreaming.ts:145`, `:230`, `:267`, `:377`, `:926`, `:980`, `:1091`).

New Conversation explicitly clears the hook, including an unbound first turn.
Abandoned sends return undefined, so the parent cannot rebind or update their
sidebar metadata after completion. Existing conversation bookkeeping is consolidated
into one helper. The hook publishes the server ID at the start frame, allowing the
sidebar to retain an interrupted first turn before its final result. No new request
ID system, execution engine, database, dependency or configuration switch was added
(`jvchat/src/components/ChatInterface.tsx:273`, `:306`, `:378`, `:406`,
`jvchat/src/hooks/useStreaming.ts:402`).

## Actual-provider browser evidence

The disposable 3136 production UI preview calls native backend 8136 and its isolated
PostgreSQL graph using actual OpenAI GPT-4.1. Execution functions were not patched.
Browser automation observes cloned responses, clicks New Conversation 500 ms after
headers, and sends the next message through the normal composer. It records the
old request's AbortError, empty reset state, enabled composer and completed new
session with native tool activity and reported usage. Authentication headers and
session capabilities are omitted.

The [initial probe](2026-11-05-jvchat-ownership-initial-browser.json) and
[its SQL/source receipt](2026-11-05-jvchat-ownership-initial-smoke.json) exposed missing
early sidebar indexing: the server ID was published only at final, despite the
parent's intended first-binding effect. After moving publication to start, the
final fresh-context probe proves the first session is indexed before reset.
The new thread contains B without A. Reopening A and sending a continuation shows
a recovery notice, with no model event/effect or provider call for that follow-up.
Returning to B restores its own response/tool/usage without A's notice.

SQL has two distinct sessions in the final cohort: the original run is cancelled
with SDK run_failed and one unknown-cost attempt; B is delivered with its own domain
task; A's follow-up is blocked before a model request. Generic Interaction.closed
and completed_at remain false/unset on cancelled turns; terminal proof is the SDK
event/native_run, while browser timing records the request abort. Lifecycle/telemetry
normalization and operator reconciliation remain open; no rollback or free billing
is inferred.

Across both probes: four completed provider responses reported 4,616 known tokens
and USD 0.008032 estimated. Two additional interrupted model attempts have unknown
usage/billing. These are not zero-cost calls, and the total invoiced cost is unknown.
Final browser timing is 559.5 ms to abort and 3507 ms for B's successful request;
backend and follow-up timings are in the receipt. Small cancellation cohorts do not
establish a performance improvement or general reliability rate.

## Validation and remaining gates

All 129 frontend tests pass (1.87 s); production TypeScript/Vite build passes
(4.35 s), and eight Python static-server tests pass (3.45 s). New tests exercise
late chunks/errors/final cleanup, reset before initial binding, sidebar/Stop followed
by another request, abandoned attachment preparation, and first-session identity
publication without aborting the preserved binding. Initial regressions fail on
the parent; the final implementation passes. All-file/commit hooks, full-suite push
and exact-head remote CI remain separate gates recorded on the live PR.

Live browser qualification here covers New Conversation during a real model attempt,
new-session success, interrupted-session refusal and transcript isolation. Late
callback injections, Stop/sidebar overlap and attachment preparation are deterministic
tests, not additional live-provider cohorts. Agent changes/unmount, all browser
transports, deployment outages, settled billing/effects and operator recovery require
further qualification. Broader Action/skill/provider/channel compatibility, migration,
legacy/pilot removal and enterprise release gates remain open.
