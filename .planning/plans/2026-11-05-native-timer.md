# Native scheduler loop and bounded subscription drain

Parent revision: `9e6e33255098132c1f625270c37bee993a4a9dca`.
Runtime source blobs are recorded in the linked PostgreSQL exports; the CLI
source under test is included. Branch: `feat/pydantic-harness-rebuild`.

## Repair and ownership

The old CLI completed bootstrap in one `asyncio.run`, optionally seeded in a
second, and then used synchronous `Server.run`, which created the serving loop.
Bootstrap-owned scheduler resources therefore retained a closed loop. The
[parent mutation result](2026-11-05-native-timer-mutation.txt) demonstrates this
with the actual published jvspatial SchedulerService: both loop cases fail with
the bootstrap loop closed; the startup-failure case still passes.

The CLI now awaits bootstrap, optional stress seed and public
`Server.run_async` inside one `asyncio.run` at
[`server.py:316`](../../jvagent/cli/server.py#L316). Action startup and dynamic
endpoint registration remain before serving. Debug reload stays disabled and
serverless retains the explicit single-worker option. Uvicorn receives a
30-second graceful connection/task drain at
[`server.py:399`](../../jvagent/cli/server.py#L399). This bounds that drain,
not arbitrary blocking application lifespan hooks or driver shutdown.
No scheduler, execution loop, persistence store or model adapter was added.

## Actual browser and PostgreSQL proof

Disposable app `/private/tmp/jvagent-native-host-context-smoke`, PostgreSQL
`jvagent_native_host_context`, backend 8136, source-built reference client 3136.
Browser isolated context `native-timer-lifetime`; actual `openai:gpt-4.1`.
One-minute JWT profile retained from the prior renewal test. The private fixture
only queues a due synthetic read task through `Agent.enqueue_proactive_task`;
it does not call `TaskMonitor.tick`, patch execution functions or write replies.
The production `system_task_monitor` registration remains every two minutes.
An attempted fixture YAML interval change was not applied by merge sync; the
observed actual timer and timestamps below establish what ran.

- Foreground browser send `NATIVE-TIMER-CHECK: Reply exactly READY.` returned
  `READY`, with visible model/token/timing statistics. HTTP resource duration
  4.437s; persisted Interaction duration 4.244122s.
- At 23:29:06.416 UTC the authenticated setup request queued
  `task_8e6f068810eb` in session `sess_ca6f2817b8614af4` (HTTP 200, 57.4ms).
  [Queued SQL](2026-11-05-native-timer-queued-smoke.json) shows pending state.
- The ordinary timer claimed the task at 23:30:15.337805 and completed it at
  23:30:19.928459 UTC. The actual SDK/MCP run looked up REHEARSAL-47 and the
  browser displayed room Cedar, start time 14:30, and
  `mcp_records__lookup_record`. Persisted Interaction duration: 4.563946s.
  Queue-to-completion was about 73.47s, including the normal timer wait.
- [Browser receipt](2026-11-05-native-timer-browser.json) records visible text,
  tool name, served asset and one foreground interaction POST plus setup POST;
  no manual tick/run request occurred. [SQL after dispatch](2026-11-05-native-timer-dispatched-smoke.json)
  proves two delivered SDK-backed Interactions and the completed task/result.
- Three provider requests total: 4,099 provider-reported tokens; USD 0.006758
  estimated cost, not provider-reported billing. Conversation spending matches;
  no remaining usage reserves in this cohort.
- With an authenticated subscription parked, a single SIGTERM stopped the
  confirmed backend PID in 30.672s, including scheduler and lifespan shutdown.
  [Signal/process receipt](2026-11-05-native-timer-shutdown.json) and
  [lifecycle log](2026-11-05-native-timer-lifecycle.txt) record the outcome.
  No second signal or forced kill was sent.
- [Fresh SQL after stop](2026-11-05-native-timer-stopped-smoke.json) is identical
  to post-dispatch Interactions, tasks and spending. The stopped observer sample
  is explicitly marked as source provenance, not liveness proof.
- The replacement process was restored; browser reload and conversation selection
  recovered both replies and usage without another interaction POST.
  [Browser](2026-11-05-native-timer-restored-browser.json) and
  [fresh SQL](2026-11-05-native-timer-restored-smoke.json) prove unchanged
  Interactions, tasks and spending under the replacement process.

## Checks and scope

[Python 3.11](2026-11-05-native-timer-py311.txt) and
[Python 3.12](2026-11-05-native-timer-py312.txt): 64 CLI/config/scheduler tests
passed on each. Three new tests cover actual timer dispatch and stress seed on
the serving loop in both CLI worker-option modes, plus fail-closed bootstrap.
This does not qualify an actual serverless deployment.
[Legacy dependency environment](2026-11-05-native-timer-legacy311.txt) also
passes the same 64 cases. Final staged all-files hooks pass cleanly; normal full
pre-push tests and exact-head CI remain separate publication gates.
All nine remote checks passed on parent `9e6e3325`. Frontend source did not change this round.

Still open: shutdown during active model/tools/external effects, arbitrary
blocking shutdown hooks, restart/overlap scheduling across workers, uncertain
effect and delivery settlement, cross-tab credentials, packaged client delivery,
provider/extension matrix, dependency security remediation, migration and
legacy/pilot removal. This single-worker local timer proof is not enterprise
qualification or a performance benchmark.
