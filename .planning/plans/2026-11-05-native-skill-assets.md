# Canonical skill assets and populated retrieval qualification

This increment migrates authored declarations, not the execution engine. Official
Skills remains responsible for loading and activation. The pinned 0.66.0 loader
requires canonical names matching parent directories; the public interface is
[documented upstream](https://pydantic.dev/docs/ai/harness/skills/).

## Explicit declaration changes

| Previous name/directory | Current name/directory | Scope |
|---|---|---|
| `web_lookup` | `web-lookup` | Reference app SOP, selected name and README |
| `pageindex_search_with_references` | `pageindex-search-with-references` | Reusable retrieval SOP; knowledge-ingest cross-reference |

Update callers/configuration explicitly, including library paths. There is no
silent underscore alias, runtime name normalizer or copied duplicate SOP. Existing
user-copied assets are not rewritten. Transitional legacy discovery can discover
the new names; old hardcoded names require migration. The historical inventory
keeps the old paths as baseline evidence and adds the explicit migration map.

The retrieval procedure no longer instructs `use_skill` or passes
`include_references`/`strategy` as tool arguments
(`jvagent/skills/pageindex-search-with-references/SKILL.md:43`). The existing Action
wrapper publishes query/doc_name/limit and supplies host settings itself
(`jvagent/action/pageindex/pageindex_action/pageindex_action.py:627`). Its access,
collection and model-strategy policy is not delegated to SOP text. Existing
requires-actions/allowed-tools declarations remain intact. Native host permission
checks remain independent of official metadata loading
(`jvagent/harness/native.py:90`). No new runtime code or dependency was added.

## Real browser evidence

Disposable root: `/private/tmp/jvagent-native-canonical-skills-smoke`; backend
8143, source-client UI 3136, isolated browser context `native-canonical-skills`.
Application/state uses fresh PostgreSQL database
`jvagent_native_canonical_skills_20261105`; PageIndex uses a separate disposable
JSON document index, which is the existing retrieval service, not another run
state store. Two public documents were seeded in separate collections. The
configured read Action is scoped to `canonical-public` with access checking and
reference inclusion enabled. No runtime execution function was patched.

| Attempt | Outcome | Model calls | Reported tokens | Estimated USD | Persisted seconds | Browser HTTP seconds |
|---|---|---:|---:|---:|---:|---:|
| Public Python lookup | Official skill load, real Serper search and fetch, two-sentence linked answer | 4 | 4574 | 0.008464 | 11.245911 | 11.515 |
| Retrieval before configuration applied | Wrong fixture catalog retained by merge; model honestly refused, no retrieval tool | 1 | 2573 | 0.005656 | 3.716832 | 3.9089 |
| Retry during stopped backend | Browser Failed to fetch; no Interaction or model request | 0 | 0 | 0 | n/a | 0.0006 |
| Retrieval after explicit source sync | Official skill load, existing PageIndex tool, correct date/room/code and handbook citation | 3 | 11200 | 0.013022 | 4.476413 | 4.6434 |

Successful intended workloads: 2/2 after correct setup. Whole attempted browser
cohort: two successful requested outcomes, one model-backed fixture refusal and
one transport failure. All three server Interactions are persisted delivered;
delivery status alone does not prove semantic success. Total eight model requests,
18347 reported tokens and USD 0.027142 estimated. Conversation counters reconcile
those totals with zero unpriced requests and no outstanding reserves; TaskStore
is empty. Serper service charges are not
included. Model durations displayed by the UI differ from persisted turn and HTTP
latency; both are retained rather than conflated.

The populated lookup returned 21 November 2026, Orchid room, CEDAR-83 and a
Cedar Staff Handbook reference. The other-tenant marker is absent from every
exported persisted model-context check. No legacy execution nodes/modules were
observed. This is bounded collection-isolation evidence, not a new private/member
access or complete retrieval-quality qualification. The index fixture's URL did
not appear as a clickable retrieval citation; only the returned handbook reference
was displayed. No claim of clickable citation parity is made.

The merge warning was correctly emitted but the first test proceeded before
verifying applied configuration. A source-sync attempt then exited at the
noninteractive confirmation prompt; the next browser retry hit the stopped
backend. Explicit `--update --source --yes` was applied only to this disposable
fixture, followed by graph readback proving both configured skills before the
successful retry. Existing Interactions/spend were preserved. No user deployment
or existing user graph was purged.

See [browser text/resource timings](2026-11-05-native-skill-assets-browser.json)
and [persisted SDK/result/accounting evidence](2026-11-05-native-skill-assets-smoke.json).
The evidence records parent commit and exact asset/test blobs; the containing
milestone commit identifies the completed source tree.

## Verification and remaining scope

110 focused native asset/path, configuration migration and PageIndex read-profile
cases pass on each Python 3.11/3.12. Shipped-file tests use the official loader,
actual read Action schema and deterministic library model calls, including host
allow/deny and missing-Action rejection. Initial test fixture failures (AsyncMock
being mistaken for decorated metadata, missing instructions and the SDK's denied
ToolReturnPart representation) were corrected; no runtime fix was required.
A clean-source wheel installed into an isolated target loads the canonical skill
through official Skills from outside the checkout; the old asset is absent. The
qualified existing native dependency environment was reused, so this is not a
fresh dependency-install claim. The initial build from the active checkout was
rejected because stale build/lib files included the old asset; a fresh source
tree fixed the artifact. See [wheel receipt](2026-11-05-native-skill-assets-wheel.json).
Staged all-files, commit/full-push and remote CI remain separate publication gates.

The [current reference report](2026-11-05-native-skill-assets-reference-blockers.json)
now has 18 blocked requirements; the migrated web lookup is admitted by format
preflight. Script-backed assets, remaining names/specs/policies, actual channel
behavior and model-service consumers remain blockers. Historical graph/task/spend
migration, arbitrary private/tenant configurations, complete PageIndex model/write
operations, a wholly fresh dependency install for this artifact and final legacy removal
remain unqualified. No general performance or enterprise claim is made.
