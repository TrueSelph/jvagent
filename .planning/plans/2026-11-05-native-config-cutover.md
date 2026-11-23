# Finite configuration catalogs and explicit engine-control retirement

Parent revision: `0ecac1b6`; all nine remote checks passed at that parent.
This increment extends configuration migration. It does not migrate an existing
graph or authorize deleting active legacy tasks, checkpoints or receipts.

## Native candidate semantics

`jvagent/cli/harness_migration_skills.py:48` resolves finite app/library selections
without a legacy routing engine. Declared members cannot be silently narrowed by
repeated `--skill` arguments. Authored app names override library names; an app
alias that needs migration cannot fall through to a less restrictive library
skill. Inventories are bounded to 256 entries and 256 KB per SKILL.md. Directory
and SKILL.md escapes are refused. Dynamic/wildcard selection remains blocked.

The existing JV policy projection and public official Skills loader validate the
selected declarations before a candidate is emitted. Unsupported inherited
access/task policy, invalid tool declaration shape, naming/spec/frontmatter and
Action-base migrations block. Runtime Action/tool/channel prerequisites still
need qualification; format validation is not a compatibility claim. The command
requires the native-harness extra; missing dependencies return a redacted blocker.
The separate negative dependency test also runs in profiles without that extra.

Native `library:<directory>` references resolve installed assets independently
of app root/cwd (`jvagent/harness/skill_assets.py:56`). They cannot traverse paths
or follow directory/SKILL.md symlinks out of the package. No library skill body
is copied or rewritten. The unchanged packaged research skill is used below.

`jvagent/cli/harness_migrate.py:77` explicitly reports retirement of typed light-
model gearing, synthetic acknowledgement/loop progress, lean pre-surfacing and
turn-count history controls. Native single-model execution, SDK compaction/events
and official skill loading replace those engine heuristics. These changes are
reported for review, not described as exact cost/context/UX equivalence.
Typed inactive pilot settings retire only under the legacy driver; active legacy
budgets migrate independently. Empty/disabled controls may retire; active channel
overrides, skill-only gates, vision, flow locks, planning and positive per-tool
timeouts remain blockers. A positive max_statement_length becomes a soft trusted
instruction; model output is never character-truncated by the converter.

The canonical Serper read service is now a reviewed model-service-retirement
consumer. Its implementation/base class were checked to use REST search rather
than an LM service (`action/web_search/serper/serper.py:60`, `base.py:19`). Unknown
consumers or custom provider/credential/transport policies still block retirement.

## Offline rehearsal and retained setup failure

An isolated old declaration included Orchestrator, an OpenAI model service,
three app/library skills and modular MCP/Serper/fetch/reply Actions. The CLI
generated a separate private candidate with all three skill members preserved,
26 explicit retirements and only the qualified simple model service removed.
The [plan receipt](2026-11-05-native-config-cutover-plan.json) proves source
bytes unchanged and zero graph nodes after planning; the output is mode 0600.
The operator then copied that candidate into the disposable fixture explicitly.

The first rehearsal script incorrectly parsed CLI stdout as pure JSON. The CLI
had an environment-loading log prefix. A sequencing mistake then started the
isolated old declaration before candidate installation. The process PID/command
was verified and stopped before any browser/model request. Readback found zero
Interactions and logs contained zero model requests. That setup failure remains
in the plan receipt; its unused graph was removed only after verifying no active
connections and zero Interactions. A separately created final graph was checked
empty before the successful planning/install/start sequence. No existing/user
graph was purged. The CLI Python planner mapping is directly structured; stdout
capture must account for the logging prefix.

After numeric-validation and JV-projection hardening, a separate re-plan on a
copy of the same legacy declaration reproduced the installed candidate exactly.
Its report/compiler hashes are recorded, and live results/spending were unchanged.
Runtime startup hashes identify the executed native resolver/factory modules;
export-time compiler hashes may differ because the offline hardening happened
after startup. No execution function was patched in either runtime.

## Actual-provider browser and persisted state

Final fixture: `/private/tmp/jvagent-native-config-cutover-smoke`, PostgreSQL
database `jvagent_native_config_cutover_final_20261105`, backend 8142, source-client
preview 3136. The native graph has no Orchestrator/OpenAI-service execution nodes.
Its catalog is two app-relative skills plus `library:research`. Runtime module
observation finds no legacy execution engines loaded.

All four GPT-4.1 turns deliver in one conversation. The browser shows official
skill loads, MCP lookup, real Serper search and successful page fetch, citations,
attempt counts and usage. Research uses the unchanged packaged SOP. The final
ordinary greeting is not forced through research output validation.

| Scenario | Outcome | Model requests | Reported tokens | Estimated USD | Persisted seconds | HTTP seconds |
|---|---|---:|---:|---:|---:|---:|
| Greeting | Delivered | 2 | 596 | 0.001354 | 4.629184 | 4.8245 |
| MCP REHEARSAL-47 | Delivered; Cedar, 14:30 | 3 | 1552 | 0.003494 | 6.176238 | 6.3744 |
| Search/fetch research | Delivered; Python.org source citation | 4 | 8458 | 0.018410 | 10.418129 | 10.6048 |
| Ordinary follow-up | Delivered; Good night! | 1 | 3904 | 0.007838 | 2.684825 | 2.9073 |

Total: 10 model requests, 14510 reported tokens and USD 0.031096 estimated.
Fresh PostgreSQL readback reconciles those totals with zero unpriced requests
and no outstanding reserves. Serper HTTP search and source fetch returned 200;
Serper service pricing is outside the model-cost total and is not estimated here.
TaskStore is empty; this is not an old in-flight-task migration test.
See [visible browser/resource evidence](2026-11-05-native-config-cutover-browser.json)
and [SDK/result/usage readback](2026-11-05-native-config-cutover-final-smoke.json).
Snapshot metadata/counts/digests are retained without duplicating fetched pages.

## Regressions and remaining gates

Migration/CLI/scaffold/path suites pass 263 cases on Python 3.11 and 263 on 3.12;
native/host/read-tool/real-PostgreSQL suites pass 404 cases on each version. The
suites overlap and must not be summed as unique cases. Final all-files/commit/
full-push and exact-head CI remain separate publication gates.
An isolated unknown-control guard removal fails five cases (one still refuses
its invalid inactive cap); overriding app precedence fails its adversarial case.
Tests cover typed retirements, unchanged active budgets, explicit selection,
override aliases, missing dependencies, unsupported JV policy, traversal/symlink
escape and very large inactive numbers without float overflow.

The [current reference report](2026-11-05-native-config-cutover-reference-blockers.json)
has 19 blocked requirements: five unmapped controls, four active policies, nine
skill format/name/spec/policy migrations and unqualified model-service consumers.
This refines the earlier 31 coarse controls; it is not a like-for-like percentage
or a successful reference migration. Stored graph/state/usage migration, old
selector expansion, scripts, native planning/vision/channel/concurrency policy,
remaining compatibility, release cutover and legacy deletion remain open.
No performance optimization, cloud deployment or enterprise qualification follows
from this fresh-graph configuration rehearsal.
