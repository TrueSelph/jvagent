# Interview module hosting and PostgreSQL CI repair

Parent `749ccf28`; branch `feat/pydantic-harness-rebuild`; author `eldonm`.
[Live evidence](2026-11-05-interview-host-smoke.json),
[UI observation](2026-11-05-interview-host-ui.json), and
[next shared-host extraction](2026-11-05-shared-host-extraction.md).

## Changes and tests

Importing InterviewAction previously imported the old tool executor directly and
loaded the entire orchestrator through its metadata registry. The import-isolation
regression first failed. Its visitor lookup now uses the shared context, and the
orchestrator archetype/endpoint export resolves lazily
(`jvagent/action/interview/engine.py:15`, `orchestrator/__init__.py:29`). Explicit
legacy imports still resolve to the same class and register endpoints. Bare
metadata imports no longer initialize those legacy endpoints or execution classes;
the supported Action loader requests the declared archetype explicitly.

Interview domain behavior is unchanged: no native interview activation, field
writes, validators or completion hooks have been enabled. The installed module's
tools remain outside this skill's allowlist. Hosting is a prerequisite, not full
interview compatibility.

Parent CI had eight green checks and one native Python 3.11 failure: cold edge-table
DDL timed out under the shared 200 ms test fixture deadline. Normal PostgreSQL
setup/reload now gets ten seconds; all five deliberate locked-write cases explicitly
retain 200 ms and the original two-second assertion
(`tests/harness/native/test_postgres_store.py:55`, `:133`). Both six-test real-PG
slices passed. The service healthcheck now names its configured database/user,
removing unrelated root-role probes (`.github/workflows/test-jvagent.yaml:83`).
The new head still requires exact-head remote CI; this does not erase the parent
failure or change production database deadlines.

Local native/profile/PostgreSQL suites: 195 passed on each Python 3.11/3.12
(20.12s / 21.30s, 121 warnings each). Interview/import slice: 283 passed (3.17s). Legacy
interview/loader/orchestrator slice: 1,201 passed, 7 skipped, 13 warnings. All-files
hooks and the full push hook remain mandatory for this milestone.

## Real browser result and architectural finding

A fresh disposable app/database/browser context on API 8135 and jvchat 3135 installs
InterviewAction alongside native MCP and ReplyAction. Actual GPT-4.1 lookup returned
Cedar / 14:30, with visible MCP tool activity and 668 tokens. Graph readback shows
two model requests, USD 0.001624 estimated, zero recorded unknown-cost requests,
closed/emitted/delivered state, completed tool effect, six snapshots, no interview
session/tasks and confirmed MCP subprocess exit. Backend Interaction duration was
7.881691 seconds; per-request timing remains unmeasured.

The instrumented process observes module names without patching runtime behavior.
The old orchestrator loop and executor remain absent, but shared ingress and response
journaling instantiate `jvagent.harness.runtime`. The stronger live isolation gate
therefore FAILS. The earlier cold import regression does not prove full native
runtime isolation. This is recorded as the next removal target, not waved away as
successful qualification. Signed host context, graph identity/locks and delivery
semantics must be extracted safely before removing that shared dependency.

The shared legacy skill resolver also warns that native `requires-tools` metadata
is unknown while scanning for interview specs. Native admission enforces it
separately; unified declarative resolution remains a migration task. No ordinary
member, interview write/hook/continuation, shared-host extraction, distributed
recovery, migration or enterprise-release qualification is claimed.
