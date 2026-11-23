# Retire tool pins into official skill discovery

Parent `a60cfe07`. Legacy pins affect visibility, not authorization: they add
matches to the visible surface, then permission/deny/skill gates apply. Sources:
`jvagent/action/orchestrator/orchestrator_interact_action.py:1086`, `:3036`.
The native path uses official Skills activation and JV host permission instead
of carrying that second surfacing mechanism forward. Sources:
`jvagent/harness/native.py:288`, `:292`, `:119`.

## Migration contract

Global and exact-channel `pinned_tools` lists now retire explicitly. Their
patterns never become native tool grants, instruction text or a wildcard
expander. Malformed lists, empty/padded patterns and nonstring members block.
Existing active `denied_tools`/`skill_only_tools` declarations still block
conversion; retiring pins cannot erase them. Sources:
`jvagent/cli/harness_migrate.py:68`, `:92`, `:120`,
`jvagent/cli/harness_migration_channels.py:13`, `:154`.

This intentionally removes the legacy promise of first-request visibility.
Reports require qualification of formerly pinned Action tools through the
selected skills and host permissions. Add explicit tool ownership to authored
skills where needed; candidate generation does not prove that every formerly
pinned integration is reachable or behaviorally qualified. No runtime interpreter,
provider, clock, permission path or dependency changes in this increment.

See the [migration runbook](../runbooks/native-harness-migration.md),
[machine evidence](2026-11-07-native-pin-retirement-smoke.json),
[browser record](2026-11-07-native-pin-retirement-browser.json) and
[reference refusal](2026-11-07-native-pin-retirement-reference-blockers.json).

## Public CLI and actual-model qualification

The public CLI generates a new 0600 candidate from legacy global `*` and channel
`deadline_probe__*` pins. Source digest is preserved. Modular Action declarations,
including named tool denial, remain exactly unchanged. The candidate uses a
deferred skill catalog and no pins. It runs only in a disposable PostgreSQL app
on backend8182; a four-file custom Action supplies local read/write probes.

Five GPT-4.1 browser turns across four conversations establish:

- Two allowed reads load the official skill, call the ordinary decorated Action
  method and report the actual file value `PIN-READ-VERIFIED-46`. The provider
  sees only `load_capability` before activation, then the owned read/write tools.
- A request for the unowned slow-read tool reports it unavailable. The tool
  never appears in provider schemas and its body never runs.
- Two explicit write requests reach the host denial. Both persist
  `mutation_access_denied`, SDK failed tool receipts and a clear denial reply.
  The write body runs zero times and no marker file exists. Former pins grant
  neither ownership nor named permission.

Eleven model requests/4,498 provider-reported tokens/USD 0.010136 estimated cost
reconcile with the four conversation journals; unknown-cost calls are zero.
Cold restart preserves the original three interactions/two conversations exactly.
Final cached reload preserves all five interactions/four conversations and run
registrations exactly, including denial text and usage. Outcomes, duration,
tool schemas/events, probe audit and fixture/source hashes are recorded.

The initial three turns used historical frozen client3140, which showed the old
success icon for the denied tool. That was not evidence about current UI source.
Final read/denial turns use owned frozen client3143, byte-identical to the
qualified deadline UI artifact. Only its exact origin was added to private CORS.
The current client shows a warning rather than a success check. Its generic
`Unconfirmed tool` label is less precise than the known host denial because this
fatal SDK exception emits no FunctionToolResultEvent. Distinct denied-tool status
projection remains open; the final denial and failed persisted receipt are clear.

## Verification and remaining gates

Both installed Python 3.11/3.12 migration lanes pass 216 cases. The transitional
suite passes 5,139, with 73 skips and 31 warnings; its optional SDK skips do not
substitute for the installed migration lanes. Runtime source remains unchanged
from the qualified parent.
Weakening pin validation to ordinary text-list acceptance makes six malformed
pin regressions fail. Source bytes are restored before rerunning both installed
migration lanes and all-file checks.

The unchanged reference still has three blocker categories. Within channel
policy, only `tool_call_timeout` remains blocked; script image/resource policy
and model-service caller review are separate. Ten selected skill formats pass
admission, which is not whole-app qualification. Nonzero tool/delegation/service
deadlines, retained-state cutover, legacy/pilot deletion, remaining compatibility,
precise denial-event presentation, deployment/recovery and comparative evaluation
remain open. No enterprise qualification or measured optimization is claimed.
