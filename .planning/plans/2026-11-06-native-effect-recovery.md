# Operator-verified native local write recovery — 2026-11-06

This increment advances W3/W4 of the [execution plan](2026-11-04-harness-rebuild-execution.md).
It qualifies continuation after one local text mutation whose current result can
be independently verified. It does not complete the rebuild or release gates.
[SQL/source evidence](2026-11-06-native-effect-recovery-smoke.json) and
[browser readback](2026-11-06-native-effect-recovery-browser.json) identify the
parent revision and tested source blobs; publication is a separate gate.

## Ownership and verification

Official immutable SDK run registration now carries server-established worker
and graph identity. Recovery requires the original PID to be absent on the same
OS boot/PID namespace; changed epochs alone cannot establish termination. Live,
foreign, reused or unbound owners are refused. Unsupported boot identification
leaves ordinary execution available but refuses this recovery. This is local
process-death evidence, not a distributed fence. Sources:
`jvagent/core/execution_owner.py:14`, `jvagent/harness/step_store.py:31`.

The first actual crash exposed process-varying uuid.getnode identity on this Mac.
The original file/run is retained and remains ineligible. The repair uses the
Linux boot UUID/PID namespace or macOS kern.bootsessionuuid. A regression starts
a separate interpreter and verifies stable host identity with a distinct epoch.
Source: `tests/core/test_native_worker_owner.py:57`.

Before a write, the Action contributes a root path/device/inode scope to the
SDK effect correlation key. It does not initialize unused storage at run startup.
The enabled domain adapter verifies bounded caller-scoped content and public
storage metadata before/after reading. Approval binds that resource version,
original SDK history/events, graph scope and known usage to a digest; modified
content, metadata, root or ownership invalidates it. Matching content proves the
current state, not that the original call completed. Sources:
`jvagent/harness/native.py:124`,
`jvagent/action/file_interface/file_interface_action.py:40`,
`jvagent/harness/effect_recovery.py:48`.

## SDK continuation and administrative boundary

Admin GET `/api/actions/{action_id}/native-recovery/{interaction_id}/effect`
inspects facts under the conversation lock. POST to the sibling `effect-approval`
accepts `{"approval":{"digest":"sha256:...","evidence":"...","reason":"..."}}`.
The HTTP wrapper requires the named approval envelope. Actor identity comes from
authentication. The POST strictly flushes prepared/approved receipts, never runs
a model and never changes the old SDK effect/status. Identical retries retain the
same receipt; a changed decision is refused. Sources:
`jvagent/action/native_harness/endpoints.py:308`,
`jvagent/harness/effect_recovery.py:235`.

The next explicit user turn rechecks the evidence and passes public fork_run
history plus DeferredToolResults/ToolReturn to Agent.run. No synthesized failure,
checkpoint rewrite or second execution engine is used. Host-validated blocked
user inputs are carried after the SDK pairs its pending call. That first resumed
turn excludes mutation definitions and independently denies their invocation.
Subsequent ordinary turns retain normal named write authorization. Sources:
`jvagent/harness/recovery.py:44`, `jvagent/harness/native.py:98`,
`jvagent/harness/native.py:246`, `jvagent/harness/native.py:469`,
`jvagent/action/native_harness/native_harness_action.py:391`.

The SDK owns execution and deferred-result application. JV's additional policy
exists because only the host/domain can establish local process death, authorize
an operator and verify the external file. It grants no generic replay permission;
a future public upstream domain-verification policy can replace this adapter if
it preserves these scope and authorization contracts.

## Actual provider/browser results

Fresh disposable PostgreSQL app, backend 8151 and source-built jvchat 3140, actual
OpenAI GPT-4.1. Execution functions are not patched. The monitor verifies the
owned process command and kills only after observing exact file content.

| Scenario | Persisted/visible result | Interaction seconds |
|---|---|---:|
| Initial identity attempt, physical write then kill | Running original; no acknowledgement; later approval refused | Unmeasured |
| Initial blocked follow-up | Recovery notice, zero SDK/model records | 0.104646 |
| Boot-identity repair, fresh session write then kill | Durable started effect and exact 20-byte verified.txt | Unmeasured |
| Follow-up before approval | Recovery notice, zero SDK/model records; ORCHID-62 input retained | 0.087297 |
| Admin inspect/approve/retry | HTTP 200; identical retry receipt; no model execution | Not measured |
| Explicit resumed request | Delivered verified acknowledgement and recalled ORCHID-62; one model call, no new effects | 2.729133 |
| Ordinary subsequent write | Delivered next.txt acknowledgement; completed new SDK write effect | 3.497630 |

Fresh SQL verifies the original file's content, inode and nanosecond mtime are
unchanged. The approved source digest still matches the entire original execution
facts. Original run/effect statuses remain running/started; the separate approval
does not pretend to finalize them. Both blocked turns registered no SDK run or
spend. Five reported model receipts total 3085 tokens and USD 0.007082 estimated;
conversation ledgers match. These are not provider invoices. The flat first POST
was rejected 422 before the correct named envelope succeeded. An initial test-user
email using the reserved .test domain was rejected; a valid example.com member
then received 403 on both recovery endpoints, and anonymous requests received
401. Browser CORS hides these error bodies; server access logs establish status.

Deleting only the completed session's local transcript and selecting it after
reload restores both completed replies, tool groups and usage. Fresh SQL verifies
all six Interactions, four SDK registrations, tasks and spend remain unchanged.
Interrupted and blocked replies are outside that completed-reply projection.

## Tests and open gates

Native/host/real-PostgreSQL suites pass 485 on Python 3.11 (92.51s) and Python 3.12
(final re-run 101.57s). The focused owner/effect suite passes 16 (7.85s), including separate
process identity, stale scope/resource evidence, preserved SDK facts, idempotent
approval, blocked-input recall, read-only model tool definitions and failed flush
leaving only prepared authority in the current cache. Counts overlap. All-files
hooks found and repaired formatting and two typing errors; a clean final re-run,
normal push/full suite and exact-head CI remain separate publication checks.

Only one reviewed foreground local text write with known provider receipts is
qualified. Combined unknown billing plus mutation recovery remains refused;
existing input-only reserves do not resolve this case. Other effects, multiple
mutations, domain tasks, children/background writes, S3, distributed ownership,
active storage threads, storage outages, hostile OS symlink races and provider
invoice correction remain open. Earlier unbound records are not retroactively
trusted. The proof is matching current file state, not causality or exactly-once.

jvchat currently groups a restored SDK tool result under "Used tool". Persisted
SDK facts prove no second execution, but a distinct recovery label remains an
observability refinement. Original interrupted/blocked history restoration also
remains open. No provider, deployment, migration/removal, measured optimization
or enterprise release gate is closed by this increment.

The affected transitional File Interface/interview/history-import cohort passes
321 with one skip (2.81s). The scanner identified an unprefixed file digest as
high entropy; evidence now gives raw file digests an explicit sha256: rendering
prefix and declares the reversible encoding. SQL assertions precede rendering;
no scanner hook or rule was disabled.

The final staged all-files hook run passes without modifications, including
black, isort, flake8, mypy and secrets. The updated machine-readable inventory
retains the open File Interface classes rather than treating this subset as
full compatibility.
