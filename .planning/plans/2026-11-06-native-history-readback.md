# Approved historical transcript readback — 2026-11-06

Parent `069b09e0` has all nine remote checks passing. This increment closes the
bounded jvchat text-backfill gap identified by the earlier import rehearsal. It
does not finish historical state migration, legacy deletion or enterprise release
qualification. [SQL/source evidence](2026-11-06-native-history-readback-smoke.json)
and [browser observations](2026-11-06-native-history-readback-browser.json) retain
the failed setup/continuation attempts as well as final success.

## Changes and boundaries

The existing authorized reply subscription now projects approved historical
user/assistant text alongside completed native replies. It uses the protected
import receipt, public SDK message validation and unchanged scoped Interaction
records. Historical text receives stable readback IDs, not new execution or
provider receipts. No old tool call is replayed. Source:
`jvagent/action/response/native_replay.py:9`.

The import module exposes one canonical read-only validation function. Execution
admission still separately verifies the historical spend baseline; reading a
transcript cannot authorize another model request or modify counters. Approved
IDs must be distinct and bounded. Malformed/rolled-back/foreign approvals and
changed/unpublished rows are excluded. A bad historical receipt does not hide a
separately qualified completed native reply. Source:
`jvagent/harness/history_import.py:516`, `:570`.

Final response frames include only the paired plain user input and its timestamp.
jvchat merges that input with the answer, deduplicates an existing input, preserves
cached content and uses the existing chronological turn-ordering function. An
unambiguous final answer can adopt the server's stable ID; multiple Action message
rows retain their identities. Historical tool statistics and invoice costs are
not fabricated. Source: `jvchat/src/lib/channelMessages.ts:7`,
`jvchat/src/lib/messageHistory.ts:30`.

A generic run failure previously persisted neither exception class nor safe
origin. The handler now records those facts and logs only class and source
filename/line/function, without raw exception text, provider bodies or credentials.
The user-facing notice remains generic. Source:
`jvagent/action/native_harness/native_harness_action.py:511`.

## Browser and persistence evidence

The initial fresh browser used a new PostgreSQL clone on port 8145 and the
separate built reference client on 3140. The attempted preview port 3137 was
occupied: strict startup refused it, and the existing process was left untouched.
An automation argument error affected only synthetic fixture login; actual login
then completed through the form. No production credentials are in the artifacts.

With an empty message cache and an index entry only, the browser restored seven
user/assistant pairs, including five approved legacy turns and two completed
native turns. The rolled-back conversation restored nothing. SQL readback proved
18 original Interactions, two registrations, import receipts, tasks and spending
unchanged. Historical replies showed no invented model statistics; the two
native replies retained their existing statistics.

The next continuation failed before SDK registration. A separate fresh session
still delivered READBACK-READY through actual GPT-4.1: 242 reported tokens,
USD 0.000520 estimated, 2.027091 seconds at the SDK request boundary and
2.807736 seconds for its Interaction. The initial unclassified failure remains
retained with incomplete usage, rather than being silently deleted or priced at
zero. The new diagnostic handler and a second independent clone identify
UserError at the SDK's unresolved-workspace guard, again before registration.

The cloned native messages referenced the original workspace path, while the
new application declared a different path. The pinned library correctly refuses
that implicit relocation. No workspace resolver, history rewrite or forced new
workspace was added. A deterministic regression confirms the moved workspace
fails before another model call/registration, preserves its messages and permits
continuation at the original configured workspace. Source:
`tests/harness/native/test_context.py:42`. Operator-approved workspace relocation
and recovery of these pre-registration failed frontiers remain open migration
gates. The two failed clones and their data/logs were preserved; their owned
servers were stopped normally.

Final qualification uses the existing disposable migrated application on 8144,
with its original configured workspace, final-source backend restart and a new
isolated browser on 3140/page 44. Other user applications were untouched. Only
the conversation index was seeded; no transcript messages were written manually.
The empty-cache restore and rolled-back exclusion pass again.

An actual GPT-4.1 question, without the answer in the prompt, asks for the earlier
rehearsal date in ISO format. It delivers **2026-11-18** with 523 input/eight output
reported tokens, USD 0.001110 estimated, a 1.527562-second SDK model request and a
2.527119-second Interaction. No tool call was needed. Browser end-to-end timing
was not measured. Totals across this round's two recorded provider requests are
773 reported tokens and USD 0.001630 estimated. The two pre-registration refusals
remain distinct; no provider traffic was observed for them and no invoice-zero
claim is made.

After clearing only the message cache, reloading and selecting the same session,
the browser restores **16 distinct rows: eight user/assistant pairs**, five
historical replies and three native replies with statistics. Reconnect does not
duplicate them. SQL proves the reload changed no Interactions, registrations,
approvals, tasks or spending. All 18 pre-round Interaction rows remain identical;
only the explicit successful continuation adds a nineteenth Interaction and third
registration in this fixture.

## Validation and remaining work

- Native/host/real-PostgreSQL: 457 pass on Python 3.11 in 74.34 s, plus the new
  workspace regression passing separately. Python 3.12 runs all 458 cases in
  84.69 s, with 296 warnings. Earlier pre-diagnostic suites passed 456 on each.
- Frontend: all 174 tests, lint and production build pass. The UI was built to
  a separate disposable output directory; this is source reference-client
  qualification, not a new packaged-wheel frontend deployment.
- Two new boundary regressions fail against isolated parent source: missing
  approved historical readback and missing safe failure classification. They
  pass on current source. The workspace test protects an existing upstream
  safety boundary; it is not represented as a newly repaired SDK defect.
- All-files hooks reformatted Python test files on initial runs. Those runs
  failed until changes were staged and clean hooks rerun. Final evidence-file,
  commit, normal full pre-push and new-head remote gates remain separately required.

Still open: server conversation discovery, history outside the 20-Interaction
subscription window, failed/uncertain delivery, historical tool/cost backfill,
attachments, arbitrary cursors, edited-branch/cross-tab reconciliation, other
channel renderers, workspace relocation, operator recovery of unregistered failed
runs, mixed/task/effect migration, remaining compatibility, legacy/dependency
removal and workload/release qualification. The full rebuild goal stays active.
