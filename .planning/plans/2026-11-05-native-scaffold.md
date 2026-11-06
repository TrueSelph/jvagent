# Native default scaffolding qualification

Parent revision: `cfb0bc8e`; all nine remote checks passed at that parent.
New application and agent creation now default to native execution. Existing
agent configurations are not migrated by this change. Explicit legacy profiles
remain transitional until their compatibility and deletion gates pass.

## Implemented change

- `jvagent/cli/app_commands.py:122`, `jvagent/cli/agent_commands.py:29` and both
  scaffold context defaults select the packaged `native` profile.
- `jvagent/scaffold/builtin_profiles/native.yaml:2` declares NativeHarnessAction
  and ReplyAction only. It uses native OpenAI GPT-4.1, bounded SDK limits and
  one explicitly loaded conversation SKILL.md, with no Orchestrator or separate
  model-service declaration.
- `jvagent/scaffold/yaml_io.py:91` expands the agent reference into an app-relative
  skill path. `operations.py:394` creates a valid starter, preserves an existing
  authored SKILL.md during force regeneration and refuses symlink escape using
  the existing containment helper. Agent creation does not rewrite another
  agent's YAML.
- Generated app/agent README and native Docker examples include the native extra
  and Python floor. The quickstart names JWT/admin/provider secrets explicitly.
  Custom profile examples extend native rather than adding old intro routing.
  Top-level guidance reflects the new default and the transitional release state.

This changes declarations and packaging/onboarding, not the SDK execution loop.
No dependency or generic execution engine was added. The original released
artifact remains the rollback boundary.

## Packaged artifact and real browser evidence

A wheel was built, installed with `[native-harness]` into a new Python 3.11.15
environment and dependency-checked: 80 packages, all compatible. Exact installed
versions include jvagent 0.1.8rc20, jvspatial 0.1.1, Pydantic core 2.66.0,
official Harness 0.66.0 and OpenAI 3.46.0. The wheel digest and installed paths
are in the [readback receipt](2026-11-05-native-scaffold-smoke.json).
Seven changed/default execution and scaffold assets match checkout bytes.

The installed CLI, invoked outside the checkout with no profile override,
generated `/private/tmp/jvagent-native-scaffold-smoke`. Its agent YAML was not
edited after generation. Only private runtime credentials and isolated
host/port/graph settings were supplied. The installed wheel launched backend
8141 from that app directory; jvchat source preview 3136 exercised real calls.
Fresh JsonDB graph readback initially contained eight nodes, including exactly
one NativeHarnessAction and ReplyAction and no legacy execution/model-service
node. Runtime module observation found no legacy execution engines loaded.

Both authenticated jvchat turns returned JUNIPER-64, including recall on the
second turn. Each used one actual GPT-4.1 request. No application tools are
declared by this starter, so no external tool event was expected or observed.
The typed SDK snapshot includes the authored starter instructions.

| Turn | Outcome | Reported tokens | Estimated USD | Persisted duration s | HTTP duration s |
|---|---|---:|---:|---:|---:|
| Remember ticket ID | Delivered | 270 | 0.000588 | 3.955678 | 4.115600 |
| Recall ticket ID | Delivered | 309 | 0.000666 | 1.787740 | 2.110300 |

The authenticated conversation reconciles two requests, 579 tokens and
USD 0.001254 estimated, with no unpriced requests or outstanding reserves.
See [visible browser responses and resource timings](2026-11-05-native-scaffold-browser.json)
and the separate-process JsonDB readback linked above. Snapshot summaries retain
metadata/counts/digests and a starter-instruction check without duplicating full
prompt payloads. No provider invoice cost is claimed.

## Identity probes and retained test-premise correction

An initial browser fetch probe incorrectly assumed all anonymous requests should
be refused. The existing public Mode B contract intentionally permits fresh
anonymous conversations (`session_token.py:520`); native mode enforces ownership
when they are resumed. That fresh request returned HTTP 200, made one actual
model call, and is retained in the evidence: 307 tokens, USD 0.000902 estimated,
7.143631s persisted duration. It was not silently excluded from usage totals.
Whole provider-backed cohort: three delivered requests, 886 reported tokens,
USD 0.002156 estimated, across bearer and server-established anonymous identities.

Two subsequent negative probes exercised the actual ownership contract:
resuming the bearer-owned session without a capability and presenting an invalid
bearer both returned 401 (HTTP resource durations 53ms and 16.6ms). Fresh graph
readback contains no Interaction for either refused marker and no added spend.
No auth capability, bearer token or secret is exported. This default is not a
private-only ingress policy, and the documentation now says so explicitly.

## Automated and publication checks

Affected scaffold/CLI suites pass all 200 collected cases on Python 3.11 and
all 200 on Python 3.12. New tests cover both CLI defaults, per-agent paths,
untouched prior agent YAML, official Skills loading, force preservation,
file/directory symlink escape and native/legacy Docker dependency selection.
An isolated old-default mutation fails the CLI regression; an isolated authored-
asset overwrite mutation fails both force cases. No mutation touched the browser
backend. The first invocation named a nonexistent test path and collected no
tests; subsequent valid runs and terminal exit codes establish the results.

Initial all-files hooks reformatted app_commands.py and flagged the static path
placeholder as a high-entropy string. The formatter result was staged and the
known non-secret template received a narrow inline allowlist. All-files hooks
then passed clean. The wheel was rebuilt/reinstalled after those source changes.
Final onboarding-only documentation edits postdate that wheel; this round proves
packaged runtime/profile assets, not a final release artifact or deployment.
Final staged hooks, commit/full pre-push and exact-head CI remain separate gates.

## Remaining scope

This closes default execution selection for newly scaffolded apps/agents in the
tested local single-worker profile. Existing graph migration, 31 reference
configuration blockers, legacy profile/engine/dependency deletion and default
release cutover remain incomplete. ReplyAction still has transitional inherited
model settings in persisted state; the native runs do not invoke them. Their
removal requires caller migration rather than adding empty override knobs.
Generated Docker dependencies were checked as text; no container/cloud deployment
is qualified. Broader extension/provider/channel compatibility, workload/quality
comparison, long sessions, distributed recovery, dependency security and the
enterprise acceptance gates remain open. No optimization or enterprise claim
is made from this bounded default-profile smoke.
