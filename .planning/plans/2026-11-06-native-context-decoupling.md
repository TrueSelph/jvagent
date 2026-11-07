# Modular Action context without the legacy executor — 2026-11-06

Parent `7438259d` has all nine remote checks passing. This increment removes 28
Action-to-executor import sites while preserving the existing caller-context
functions and tool contracts. It advances the service-extraction/deletion ledger;
the legacy execution implementation itself is still present and must be retired
after the remaining compatibility/migration gates. No execution engine, provider
translation, runtime configuration or dependency is added here.

## Dependency removal ledger

These imports now use `jvagent.tooling.context` directly. They previously obtained
the same functions through `jvagent.tooling.tool_executor`.

| Action | Removed import sites | Current source |
|---|---:|---|
| Handoff | 18 | `jvagent/action/handoff_action/handoff_action.py:544` |
| Skill Hub | 4 | `jvagent/action/skill_hub/skill_hub_action.py:416` |
| Artifact Handler | 1 | `jvagent/action/artifact_handler_interact_action/artifact_handler_interact_action.py:1380` |
| Code Execution | 1 | `jvagent/action/code_execution/code_execution_action.py:39` |
| File Interface | 1 | `jvagent/action/file_interface/file_interface_action.py:20` |
| UI | 1 | `jvagent/action/ui/ui_action.py:171` |
| Vision | 1 | `jvagent/action/vision/vision_action.py:134` |
| WhatsApp | 1 | `jvagent/action/whatsapp/whatsapp_action.py:20` |

The existing service supplies task-local immutable ToolDispatchContext and the
compatibility live visitor; native execution binds it around the official run.
Aliases at the old import path remain compatible for transitional callers.
Permission checks remain the responsibility of native admission and per-call
skill/host access checks; importing a context helper grants no authority. Source:
`jvagent/tooling/context.py:11`, `jvagent/harness/native.py:95`.

The strengthened cold-process test imports all eight modules, then exercises
native host admission/publication and verifies old execution modules stay unloaded.
Its final explicit legacy import assertions preserve transitional compatibility.
The test fails on the isolated parent; a direct parent FileInterface import also
confirms that it loads the legacy executor. Source:
`tests/harness/native/test_import_isolation.py:9`.

The new modular File Interface test runs concurrent callers through the shared
service, confirms each receives its own visitor identity and verifies cleanup and
the unchanged no-context response. It stubs storage and does not qualify file I/O.
Source: `tests/action/file_interface/test_file_interface_host_context.py:15`.

| Remaining removal step | Status |
|---|---|
| Modular Actions depend on the context service rather than executor | These eight modules complete; 28 import sites removed |
| Remaining shared old-runtime branches/constants/catalog responsibilities | Continue reference tracing and replacement |
| Existing configuration, tasks, history, effects and delivery migration | Partially qualified; open cohorts remain |
| Old loop/pilot/executor implementation and explicit old profiles | Still present; deletion gate open |
| Unused model transports, prompts, settings and dependencies | Retire with their remaining callers |

## Real browser and persisted-state qualification

The new disposable PostgreSQL graph starts empty. It installs NativeHarnessAction,
MCPAction, FileInterfaceAction and ReplyAction, with a primary records skill and
only `mcp_records__lookup_record` admitted. File Interface is hosted and its tool
definitions remain compatible; no native file permission or write is added.
Source/configuration/runtime evidence:
[SQL and module record](2026-11-06-native-context-decoupling-smoke.json).

Backend 8147 serves the app; isolated browser page 45 uses the already built source
reference client on 3140. The user prompt asks the integration for REHEARSAL-47's
room and start. Actual GPT-4.1 calls the reviewed MCP tool once, with the exact
reference, then replies that the room is **Cedar** and the start **14:30**. The
browser shows the tool call, arguments, result and usage. See
[browser evidence](2026-11-06-native-context-decoupling-browser.json).

Two model requests report **691 tokens** and **USD 0.001652 estimated**. The UI
shows two attempts and 3.3 seconds of model time; the Interaction takes 5.535339
seconds. Browser end-to-end timing and provider invoices are unmeasured. SQL
confirms the delivered/closed/emitted result, one SDK registration, persisted
tool events, empty domain tasks and reconciled run/conversation spend with no
unpriced requests. The MCP audit records exactly one REHEARSAL-47 invocation;
the last fixture server PID no longer exists after the SDK lifecycle exits.
The live module probe confirms File Interface is loaded and no old executor,
Orchestrator loop, pilot runtime or HarnessRuntime execution module is loaded.
No runtime function is patched by the observer.

This qualifies modular hosting and the already-reviewed MCP read workflow.
It does not qualify native File Interface execution, other Action tools, code
scripts, external writes or live WhatsApp/channel behavior.

## File I/O prerequisite retained

The proposed native file-read expansion exposed an existing dependency issue:
the installed pinned storage implementation buffers entire files even in its
streaming method. A fresh TLS-verified download of the exact published
`jvspatial-0.1.1-py3-none-any.whl` independently confirms local and S3 providers
both call `asyncio.to_thread(list, _read_chunks())`. The wheel SHA-256 and source
digests are in the [evidence](2026-11-06-native-context-decoupling-smoke.json).
The existing File Interface helper also loads the full file before head/tail
selection (`jvagent/action/file_interface/_core.py:145`). Output truncation or a
metadata size precheck would not prove a bounded read under concurrent growth.

Prior upstream I/O work already exists: [jvspatial PR #55](https://github.com/TrueSelph/jvspatial/pull/55)
is verified merged. The project must select and qualify a corrected published
dependency before claiming bounded native file reads. All three dependency pins
remain at 0.1.1; no upstream worktree, branch or package pin was changed. A first
pip download attempt found no pip module in the local hook environment; the
independent official-PyPI download used TLS and verified the published digest.
No new filesystem access is admitted by this increment.

The current public index reports jvspatial 0.2.4. It is a candidate for separate
fresh-install/API/runtime qualification, not an approved pin change in this
increment. The project can evaluate that published release rather than assuming
the upstream fix still needs its first publication.

## Checks and remaining gates

Native/host/real-PostgreSQL suites pass **458** on Python 3.11 (76.86 s) and
Python 3.12 (83.92 s), with 296 warnings each. Affected Action suites pass
386/one skip plus 17 UI Action cases on 3.11; the combined 3.12 run passes
403/one skip in 3.03 s. These suites overlap with the focused cold/context cases;
their counts are not added into a unique total. No frontend source changed.

Initial isort changes made the first hook run fail. After staging the changes,
all-files formatting/type/secrets hooks pass. Final evidence-file/commit hooks,
the normal full pre-push suite and exact-head remote CI remain separate gates.

The first full pre-push gate then stopped at collection: the new test module's
original basename test_host_context.py collided with an existing Orchestrator
test under pytest's import mode. It is renamed to
test_file_interface_host_context.py. That failure is retained. After renaming,
full collection succeeds with 4,980 tests in 4.37 s and the paired slice passes
nine cases in 0.15 s; clean hooks pass. The unpublished milestone is amended only
after those gates, then retried through the ordinary full push gate.

Full native file I/O, remaining Action/skill/provider/channel compatibility,
historical/effect recovery and migration, old-engine/dependency deletion,
comparative workload measurements and enterprise release remain incomplete.
The full goal stays active; fewer import edges alone is not an optimization claim.
