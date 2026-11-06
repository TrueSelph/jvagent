# Native local writes and retained effect identity — 2026-11-06

Parent `77eb1ef4` has all nine remote checks passing. This increment replaces
the factory's conversation-specific write branch with a reviewed domain-mutation
declaration and qualifies a bounded local text write. The SDK still owns tool
dispatch, journaling, snapshots and effects. No execution loop, replay engine,
provider translation, database or dependency is added. Public Action names and
legacy direct file interfaces remain compatible.

## Execution and permission boundary

The [native mutation declaration](../GLOSSARY.md#native-mutation-tool) requires
host visitor binding, a stable write marker and SDK persistence. The factory
annotates the public ToolEffectRecord with its effect summary and run/tool-call
correlation key before invoking domain code, then verifies the record. Retry
control flow from a mutation becomes a terminal reconciliation error rather than
a tool retry. Delegated children exclude all these mutation tools. Sources:
`jvagent/harness/mutation_tools.py:25`, `jvagent/harness/native.py:115`, `:181`.
The existing ConversationMutationTool and marker retain their identity and graph
acknowledgement behavior (`jvagent/harness/conversation_tools.py:14`).

File Interface publishes its existing `file_interface__write_file` name and
path/content schema using this declaration. Its host-owned
`native_write_max_bytes` defaults to 262144. An explicit named AccessControl tool
grant is mandatory; absent, unenforced, unnamed or denied policy cannot fall
through to a permissive general rule. The skill's tool allowlist is separately
enforced. No additional approval framework or execution switch is introduced.
Sources: `jvagent/action/file_interface/file_interface_action.py:45`, `:131`,
`jvagent/tooling/access.py:64`.

The write validates UTF-8 byte length before provisioning, uses the shared
caller-scoped public LocalFileInterface and verifies bounded readback equals the
submitted text before returning bytes/digest. Nonlocal native writes refuse;
other file tools remain outside the qualified surface. The existing direct
helpers remain available to transitional/domain callers. Sources:
`jvagent/action/file_interface/_core.py:238`, `:256`,
`jvagent/action/file_interface/file_interface_action.py:89`.

The SDK key is an immutable correlation fact, not an external conditional write
or an exactly-once guarantee. A failed run containing any mutation marker cannot
discard its outputs and fall back to accepted input, even when the effect says
completed or failed. An abandoned running run also remains blocked. No outcome
is inferred to mean rollback. Source: `jvagent/harness/recovery.py:209`.

## Confirmed effect-ledger gap and adversarial qualification

Before repair, the graph StepStore allowed a later record with the same
`(run_id, tool_call_id)` to replace a terminal write, its tool name, summary or
correlation key. Four new direct regressions fail and one valid-transition case
passes against that implementation. The adapter now rejects changed terminal
records, changed tool identity and erasure/replacement of existing annotations;
identical retries and started-to-terminal transitions remain supported. Validation
holds the existing store lock and runs before state mutation. Source:
`jvagent/harness/step_store.py:63`.

The stronger SDK test reuses a call ID across model responses. With the isolated
parent store and current adapters, it executes a second physical write and the
protective test fails: the file contains the second payload. With the repaired
store the second invocation fails before its body, the first file remains exact,
and failed-turn recovery stays blocked. This is a demonstrated identity/replay
gap, not a claim that production GPT-4.1 emitted a duplicate ID. Same-response
duplicates are already rejected by core before I/O; the first test assumption
was corrected to exercise cross-response reuse instead.

Sources: `tests/harness/native/test_effect_identity.py:1`,
`tests/harness/native/test_file_write_adapter.py:1`. Mutation worktree and logs:
`/private/tmp/jv-native-write-effect-mutation`,
`/private/tmp/jv-native-write-effect-mutation.txt`.

The focused write/effect/file suite passes 33 cases. It includes actual local I/O,
named grant/deny/missing policy, marker presence before the body, failure after
physical write, failed readback, later provider failure, no pre-write recovery,
UTF-8 byte limits before directory creation and cross-user link refusal. The
expanded notice/write suite passes 52. Native/host/real-PostgreSQL suites pass
**478 per Python 3.11/3.12**, 85.76/85.41 seconds. Counts overlap. The initial
all-files run caught a mypy narrowing error; an explicit isinstance guard repairs
it, and the subsequent full hook set passes. Final artifacts and normal full
push/new-head CI have separate gates.

## Actual GPT-4.1 browser and PostgreSQL evidence

The fresh disposable app on backend 8150 contains native harness, File Interface,
AccessControl and Reply Actions, with explicit skill and host grants. Page 48
uses an isolated browser context on the existing source-built jvchat at 3140.
No runtime function is patched. [SQL/source receipts](2026-11-06-native-file-write-smoke.json)
and [visible browser evidence](2026-11-06-native-file-write-browser.json) retain
the initial defect and final behavior.

| Scenario | Result | Requests / reported tokens / estimated USD | Interaction seconds |
|---|---|---|---:|
| Authorized write | Exact 14-byte note, verified digest, completed SDK effect | 2 / 1094 / 0.002692 | 4.301469 |
| Live host permission revoked, before UX repair | No denied file created; misleading generic configuration notice | 1 / 675 / 0.001530 | 2.387902 |
| Same denial class after backend restart and repair | No file created; specific permission-denial notice and persisted reason | 1 / 479 / 0.001138 | 2.115196 |
| Abrupt kill immediately after physical write | Exact 20-byte file; SDK effect remains started and run running | 1 / 486 / 0.001158; unfinished run | Unmeasured |
| Follow-up after restart | Recovery notice; zero model or SDK records, no repeated write | 0 / 0 / no new spend | 0.783127 |
| Fresh conversation after restart | Exact 22-byte write, verified digest, completed SDK effect | 2 / 1097 / 0.002692 | 4.012086 |

Permission updates use the authenticated admin Action API, not a process patch.
The observed misleading denial is repaired with MutationAccessDenied and a
specific persisted reason; domain I/O permission errors are not relabelled as a
host grant failure. Sources: `jvagent/harness/mutation_tools.py:13`,
`jvagent/action/native_harness/native_harness_action.py:527`,
`tests/harness/native/test_native_action.py:45`.

The crash monitor verifies the owned process command, then sends SIGKILL only
after observing the exact expected file. Fresh SQL shows a durable annotated
started effect, one reported model receipt, one completed model request, no
subsequent request-start event and no tool completion. Its host run remains
running and is not silently terminalized. After restart the follow-up makes no
new model request, preserves the crash run/effect and leaves file content, inode
and nanosecond mtime unchanged. The successful fresh session proves the refusal
does not disable unrelated conversations.

Deleting only the fresh completed session's browser transcript cache restores its
reply, tool and statistics from graph state. Fresh SQL proves all six Interaction
hashes, five SDK registrations, tasks and spend remain unchanged. The interrupted
file also remains unchanged. No old executor, Orchestrator loop, pilot runtime or
HarnessRuntime is loaded in the observed native processes.

Seven observed provider-reported call receipts total **3831 tokens and USD
0.009210 estimated**; conversation ledgers reconcile to those receipts. The
crashed run has no final usage/response settlement and must not be presented as
successfully acknowledged. Invoice costs and browser end-to-end timing are
unmeasured. The record does not infer final settlement from absent flags.

## Remaining course and release limits

This qualifies local text-write success, named denial, failure guards and an
actual interrupted-write barrier. Operator reconciliation/resumption is still
unfinished. The public SDK recovery inspection supplies facts, not replay
permission; next implement the scoped operator inspection/decision path with
domain evidence, preserved markers and no automatic repeated effect.

Other file operations, live S3, remote providers/channels, background/child writes,
distributed ownership, hostile OS symlink races, blocked-driver deadlines,
uncertainty UX and storage metadata/orphan cleanup remain open. No invoice,
exactly-once, complete File Interface, full migration/deletion, performance or
enterprise qualification claim follows from this increment. The full goal stays
active.

Final affected transitional File Interface/interview/import slice passes **322**
in 2.40 seconds. The staged all-files gate passes YAML/JSON, whitespace, black,
isort, flake8, mypy and secrets without modifications after repair. The scanner
initially classified three opaque SDK correlation keys as entropy findings;
artifacts now render those identifiers with an explicit sdk-effect-key prefix
and declare the reversible encoding. Raw SQL equality checks precede rendering.
No scanner rule or hook was disabled. Normal full-suite push and new-head remote
checks remain separate publication evidence.
