# Native SDK scripts and existing user files

Status: bounded local development profile qualified; enterprise goal remains open.
This supersedes only the storage portion of the
[V1 script record](2026-11-06-native-script-sandbox.md). See the
[operator configuration](../runbooks/native-script-sandbox.md),
[SQL/resource evidence](2026-11-06-native-user-files-smoke.json) and
[browser record](2026-11-06-native-user-files-browser.json).

## Contract and implementation

Scripts now use the existing local FileInterface `<agent>/<user>` directory.
Official DockerSandbox binds only that authenticated caller leaf. Public SDK
Shell still owns commands, timeouts, cancellation and command results. There is
no file synchronization engine, second file store, command runner or dependency.
The extracted public storage-scoping helper retains the previous private import
as a compatibility alias. Sources: `jvagent/core/sandbox.py:147`,
`jvagent/action/file_interface/_core.py:21`,
`jvagent/harness/script_sandbox.py:104`.

Admission requires a non-root POSIX server and actual filesystem capacity at most
64 MiB. The graph binds root/device/inode/uid/gid, caller/agent/session, reviewed
image, limits and script hashes. Unsupported providers, ambiguous identity
segments, changed roots/policy and symlink roots are refused. The user marker is
a mount canary, not a credential or an authorization signature. Staging verifies
it through the SDK before copying reviewed assets; existing changed assets are
refused instead of repeatedly overwritten across conversations. The shared
filesystem ceiling does not reserve independent space for each user. Sources:
`jvagent/harness/script_files.py:32`, `:96`,
`jvagent/harness/script_sandbox.py:188`.

V1 policies remain unchanged and are refused before storage rebinding. The
published V1 implementation is a rollback artifact, not a selectable alternative
native file engine. Failed or uncertain shell effects still require operator
reconciliation; even intended reads remain conservatively classified as shell
mutations. Sources: `jvagent/harness/script_sandbox.py:209`,
`jvagent/harness/native.py:445`.

Shared staging exposed a separate lock handoff race: expiry could replace an
unlocked lock while a notified waiter still held it. The regression fails on the
published implementation. Existing MemoryLockManager now uses weak loop/key
caches; owners and waiters retain live locks without TTL maintenance, timers or
private asyncio fields. Public acquire semantics are preserved. This is process
coordination, not a distributed locking claim. Sources:
`jvagent/memory/lock_manager.py:14`,
`tests/memory/test_lock_manager_lifetime.py:14`.

## Real model/browser evidence

An isolated PostgreSQL graph, backend 8155 and source-built jvchat 3140 use the
configured OpenAI GPT-4.1 provider and a real 64 MiB host filesystem. The app-local
copy of shipped triage adds FileInterface prerequisites/tools and a shared-file
SOP; its ranking script is unchanged. Before testing, acceptance required exact
file bytes, correct descending severity, actual tool events, preserved bytes
after stop/start and no graph/spend writes during history restoration.

Eight actual model turns include seven positive workflows and one retained
pre-fix failure. FileInterface writes seed.txt; the SDK reads it and runs triage;
an explicit SDK command writes script-only.txt; FileInterface reads both script
outputs. After backend restart, another conversation for the same user reads
the original files. A final read after an actual container stop succeeds without
rewriting either file. An intervening repeat read occurred before the stop and
is counted separately, not used as restart proof.

The first stopped-container test failed because Docker rebuilt the SDK's
process-control tmpfs as root-owned 0755. Its graph retains a started script
effect and the failure notice; no retry or state rewrite was used. A public
`docker_args` tmpfs mount with explicit mode 1777 repairs the non-root command
boundary. The live regression stops and reattaches through SDK history. Remove
this pinned-upstream-gap configuration after an upstream fix passes that test;
do not replace SDK internals. Sources: `jvagent/harness/script_sandbox.py:151`,
`tests/harness/native/test_script_sandbox.py:350`.

The 15 provider requests report 20,052 tokens; USD 0.031638 is estimated pricing,
not provider-reported dollars. Zero receipts have unknown cost. Every receipt
reconciles to per-turn usage and both conversation counters, including the failed
turn. Visible history/tool summaries/statistics restore after deleting only the
isolated browser's message cache. Eight Interactions, eight SDK registrations,
tasks, planning/import state and spending remain unchanged. Failed notices are
not included in completed-pair restoration. The current process has no legacy
execution modules loaded; runtime functions are unpatched. Exact source blobs,
durations, run/effect states and mounts are in the SQL evidence.

A separate public SDK/FileInterface probe verifies bytes survive destruction
and recreation of the container and that the host filesystem refuses a write
past capacity with ENOSPC. This proves physical file durability, not automatic
continuation of a conversation whose SDK container ref has been destroyed.

## Checks and limitations

- Native/host/real-PostgreSQL lanes with Docker-positive cases enabled: 539 pass
  on Python 3.11 (109.51 seconds) and 3.12 (121.12 seconds).
- Script/file-binding/lock regressions: 33 pass, including actual stop/start.
- Transitional memory/FileInterface: 221 pass, two existing Redis skips.
- First full pre-push: 5,004 pass, 55 skip, one stale private-cache-type assertion
  fails. Replace that assertion with public same-manager lock sharing and
  independent-manager isolation; rerun publication gates before pushing.
- Staged all-files hooks pass; final documentation hooks, commit/pre-push and
  exact-head remote checks are separate publication gates.
- An initial full lane had four ENOSPC admission failures on the reused
  quota-probe filesystem. The corrected fixture uses a fresh bounded test-only
  filesystem and removes its own created roots. Failures are not passing evidence.
- Ordinary-member login succeeds but the reference client's admin-only agent
  listing returns 403 before chat. No member model call occurred. This is not
  evidence of member script isolation or authorization failure in the harness.
  Source: `jvagent/core/endpoints/agents.py:21`.

Still open: multi-user live scripts, independent user capacity policy, cleanup
and expiry, crash/timeout/cancellation process qualification, failed-script
operator reconciliation, destroyed-ref continuation, daemon/OS restart, hostile
host filesystem races, remote Docker path mapping, S3, artifact delivery and
broader image/OS/provider matrices. Changes to host mount identity need explicit
migration. Full compatibility, retained-state migration, legacy engine deletion,
security remediation and enterprise release remain open. No performance or
maintained-complexity improvement is claimed from this source change alone.
