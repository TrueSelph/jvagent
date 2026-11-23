# Official Docker/Shell script execution — 2026-11-06

Development compatibility increment, not production script or enterprise release
qualification. Parent revision `e291b8390b24739d2d469dba18ec966cffb8be5a`.
Exact runtime blobs, backend epochs, requests, timings, SDK effects, workspace
refs, usage and graph readback are in the
[source/SQL record](2026-11-06-native-script-sandbox-smoke.json); visible replies
and tool groups are in the [browser record](2026-11-06-native-script-sandbox-browser.json).

## Replacement and ownership

`CodeExecutionAction.get_native_script_sandbox()` declares an optional native
service profile. Official `DockerSandbox`, `Shell`, public `Workspace` operations
and the renamed SDK toolset own execution. JV retains `code_execution__bash`,
checks the host permission/skill boundary and stages bounded reviewed script
assets. There is no subprocess fallback, custom command scheduler, provider
protocol or second execution-state database. The Action's legacy executor import
is now lazy; qualified native backend epochs leave it unloaded. Sources:
`jvagent/action/code_execution/code_execution_action.py:84`,
`jvagent/harness/script_sandbox.py:104`, `jvagent/harness/native.py:445`.

The [runbook](../runbooks/native-script-sandbox.md) defines the explicit image,
permission, resource and storage boundary. Script files remain authored assets;
the original triage procedure and script run through the SDK. Its only asset
change is an explicit required tool declaration. `spec: claude` admission requires
that declaration and `CodeExecutionAction`; unsupported inheritance/policy remains
refused. Native child workers do not inherit this execution profile. Sources:
`jvagent/skills/triage/SKILL.md:12`, `jvagent/harness/skill_admission.py:65`,
`jvagent/harness/skill_assets.py:98`, `jvagent/harness/native.py:529`.

Image/limits and script hashes bind to authenticated agent/user/session and the
actual Conversation's graph owner using the spend-store lock. Only policy facts
are added to the graph; SDK messages retain workspace refs and SDK steps retain
execution facts. Changed policy requires explicit migration or a new conversation.
Every command is conservatively a mutation, even an intended shell read. Host
denial/read-only recovery happens before staging/execution, and the SDK mutation
record must exist before staging. Staging runs once per root run, so SDK parallel
commands cannot read scripts while another call recopies them. Sources:
`jvagent/harness/script_sandbox.py:112`, `:163`,
`jvagent/harness/native.py:294`, `:450`,
`jvagent/memory/conversation.py:119`, `:379`.

## Browser cohorts and discovered defects

Disposable PostgreSQL graph; backend 8154, source-built jvchat 3140;
actual `openai:gpt-4.1`; reviewed local Python image digest in the receipt.
No execution function is patched. The observed cohort retains all attempts,
including an incidental ordinary question that arrived during setup.

- The incidental question succeeded without a tool but exposed eager container
  allocation in the first implementation. Provisioning/staging now happens only
  at the first authorized script call. A fresh live `READY-83` turn and SDK tests
  carry no workspace ref or tool execution.
- The bundled triage script produced sorted results and saved the requested
  marker. Backend restart/readback preserved files and the SDK workspace ref.
- Actual commands showed uid 65534, an absent synthetic host-only path, absent
  `OPENAI_API_KEY` (contents never requested), and a blocked one-second TCP probe.
  Docker configuration readback separately records isolation/resource settings.
- Removing the named tool grant produced a visible permission refusal and no
  command effect; provider usage remains billed. A separate conversation could
  not see the first conversation's marker.
- After restoring permission, the first accepted-input continuation selected a
  new workspace and honestly reported missing files. This semantic failure is
  retained even though the Interaction's delivery status is `delivered`.

The latter is a confirmed JV recovery defect, not a Docker deletion or SDK fork
failure. JV's synthetic failure response omitted `workspace_ref`. Core chooses
the most recent response's ref; the empty host response therefore reset workspace
selection. `_host_notice` now carries only the latest already-authorized SDK ref,
including an explicit None, into JV notices. It neither rewrites original SDK
messages nor resurrects a reference cleared by a later SDK response. The same
helper covers accepted-input failure messages and intervening blocked notices.
Sources: `jvagent/harness/recovery.py:42`, `:132`, `:325`.

The new accepted-input regression fails against isolated published parent
`e291b839` with `None != WorkspaceRef(...)` and passes with the fix. The pre-fix
conversation is not repaired or purged to force a successful result. A fresh
post-fix browser cohort provides the replacement acceptance evidence in the linked
records. Private mutation log: `/private/tmp/jv-script-workspace-parent-red.txt`.

The post-fix create/deny/restore/restart/read cohort passes: the final SDK ref is
identical to creation and denial, the marker is `RECOVERY-WORKSPACE-84`, and the
saved order is outage then typo. All ten earlier SDK record sets remain unchanged.
Clearing only that transcript cache restores its two completed replies, tool
groups and usage without changing eleven Interactions/registrations, SDK refs,
policy, tasks or spending. Failed notices remain outside completed-pair restoration.

Whole retained cohort: eleven Interactions, nineteen provider requests, 21,414
provider-reported tokens and **USD 0.041412 estimated** from SDK pricing, zero
unknown-cost calls. Seven positive workflows are validated, two permission denials
are expected, one delivered workflow fails semantically before the fix, and the
incidental ordinary answer is not fact-checked. Nine `delivered` statuses do not
mean nine validated successes. Per-turn elapsed/model durations and all priced
receipts reconcile to the three conversation spending counters in the SQL record.

## Verification

- Native/host/real-PostgreSQL suites with Docker-positive cases explicitly enabled:
  530 pass on Python 3.11 (109.90 seconds) and Python 3.12 (112.37 seconds).
- Script slice: 20 pass, including actual SDK commands, workspace continuation,
  lazy/denied/read-only turns, once-only staging before parallel commands, mutation
  barriers, bounded assets and caller/policy refusal. Source:
  `tests/harness/native/test_script_sandbox.py`.
- Recovery slice: 25 pass; the new accepted-input test fails on published parent
  with the lost workspace reference. Explicitly cleared refs are not resurrected.
  Source: `tests/harness/native/test_recovery.py:139`.
- Native migration CLI: 80 pass. Transitional code-execution/memory: 219 pass,
  three existing environment skips (PDF engine and two Redis integration cases).
  Sources: `tests/cli/test_harness_migrate.py`, `tests/action/code_execution/`,
  `tests/memory/`.
- Staged all-files hooks, normal full pre-push and exact-head remote CI remain
  separate publication gates. Earlier hook failures from duplicate imports and
  concurrent worktree edits are not treated as clean passes.

## Configuration and deletion ledger

Offline conversion now admits the explicitly declared triage format and blocks
enabled code execution without a reviewed native image/resource policy. It does
not silently preserve a subprocess executor as native execution or mutate source
configuration. The [reference report](2026-11-06-native-script-sandbox-reference-blockers.json)
still has **17 blockers**: one skill-format blocker is removed, while a specific
code-execution policy blocker is added. Four selected skills pass format preflight;
this is not four runtime-qualified workflows. Sources:
`jvagent/cli/harness_migrate.py:273`,
`jvagent/cli/harness_migration_skills.py:160`.

Keep Action declarations and the public bash name. Retire `executor.py`, legacy
`stage_skill`/`run_bash` and old runtime/isolation staging responsibilities only
after the native storage/artifact/retained-state contract below qualifies and all
callers are traced. Those mixed-purpose legacy implementations remain transitional;
this increment does not authorize their deletion. No dependency is added. Source
delta is recorded for accounting, not presented as complexity reduction or a
performance improvement.

## Open gates

- **Storage and interoperability:** bounded tmpfs survives backend-process restart
  while the container remains running, but can lose files on container/Docker
  restart. Persistent bounded storage and stopped-container semantics are not
  qualified. Conversation workspaces are separate from the existing per-user
  FileInterface store. Artifact export/delivery, existing PDF/DOCX dependencies,
  full Claude skill compatibility and retained user files remain open.
- **Lifecycle and recovery:** container cleanup/expiry, aggregate quotas, actual
  crash-mid-command, timeout/cancellation process cleanup and script-effect operator
  reconciliation remain open. No automatic replay or exactly-once claim is made.
  Bounded asset staging is application initialization; commands stay SDK-owned.
- **Scope and deployments:** live same-user conversation isolation is qualified;
  ordinary-member multi-user script execution, child/background scripts, supported
  OS/deployment/provider matrices and hostile daemon/host filesystem behavior are
  not. Images are explicit reviewed host configuration, not model authority.
- **Automation:** Docker-positive regressions require an explicit local reviewed
  image environment variable; ordinary CI without it skips those cases. Local
  configured-Docker execution is recorded separately from default CI.
- **Qualification:** dependency/security remediation, comparison benchmarks, full
  migration/removal and the overall enterprise release gates remain active. The
  observed semantic failure is included in outcome accounting; delivery alone is
  not counted as successful completion of the requested workflow.
