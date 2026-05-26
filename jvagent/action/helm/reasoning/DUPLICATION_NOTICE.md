# Duplication Notice

Every Python module under `jvagent/action/helm/reasoning/` was duplicated
from `jvagent/action/cockpit/` per the C-strategy hard constraint in
[`.planning/BRIDGE-ROADMAP.md`](../../../../.planning/BRIDGE-ROADMAP.md) §C:
**no source-level coupling between Bridge and Cockpit**.

The duplication is faithful: only import paths are rewritten
(`jvagent.action.cockpit.*` → `jvagent.action.helm.reasoning.*`) and the
session key is renamed (`cockpit_session` → `reasoning_helm_session`) so
the two patterns cannot share `visitor._skill_state` if both are
mis-installed on one agent. Class names and constants keep their cockpit
prefixes so the duplicated files diff cleanly against their ancestors during
review. Future revisions of these files MAY diverge from the cockpit
source; treat this notice as the **last known sync point**, not an
ongoing-mirror guarantee.

## Known divergences from cockpit (post-sync)

- **IA-tail dispatch via DELEGATE chain** (BRIDGE-ROADMAP §C-6 follow-up,
  not in cockpit). `reasoning_helm.py` no longer calls
  `curate_walk_path_for_cockpit` to schedule routed `interact_actions`
  on the walker queue; Bridge owns walker-queue curation, and routed
  IAs are dispatched one-at-a-time through a `DELEGATE` chain
  (`follow_up=True` on all but the last entry, `follow_up=False` on the
  tail so Bridge runs persona-finalize). Cockpit retains its
  `curate_walk_path` flow because it IS the InteractAction in the
  walker queue. Side-effect: the helm no longer references
  `visitor._bridge_action` because it no longer needs the IA-in-the-
  queue reference for queue mutation.
- **`_finalize_via_persona` removed.** Cockpit's IA-only-mode finalizer
  has no helm counterpart — Bridge's
  `_finalize_via_persona_if_directives` runs after the last DELEGATE
  in the chain instead.
- **Always-execute IA collection removed.** Cockpit collects and
  weight-orders always-execute IAs alongside routed IAs before
  curating. In Bridge composition this is duplicative — Bridge's own
  `_curate_walker_queue` already restricts the walker queue to
  `{Bridge} ∪ always_execute IAs` on the first visit. Note: cockpit's
  pre-curate path also applied AccessControl to always-execute IAs;
  Bridge's `_curate_walker_queue` does not (yet — known follow-up).

The `tests/action/helm/reasoning/test_no_cockpit_imports.py` invariant
guard fails the build if any `.py` file under `jvagent/action/helm/` or
`jvagent/action/bridge/` imports from `jvagent.action.cockpit`.

## Source attribution (per file, by sub-milestone)

### C-2 (engine + supporting modules)

| Reasoning module | Cockpit source | Source commit |
|---|---|---|
| `contracts.py` | `jvagent/action/cockpit/contracts.py` | `4bc6db6` |
| `config.py` | `jvagent/action/cockpit/config.py` | `4bc6db6` |
| `context.py` | `jvagent/action/cockpit/context.py` | `4bc6db6` |
| `session.py` | `jvagent/action/cockpit/session.py` | `4bc6db6` |
| `prompts.py` | `jvagent/action/cockpit/prompts.py` | `4bc6db6` |
| `engine.py` | `jvagent/action/cockpit/engine.py` | `4bc6db6` |
| `registry/__init__.py` + `registry/assembler.py` | (stub — replaced at C-3) | n/a |

### C-3 (tools + registry + catalog + routing + delivery)

| Reasoning module | Cockpit source | Source commit |
|---|---|---|
| `tools/__init__.py` | `jvagent/action/cockpit/tools/__init__.py` | `fbb5136` |
| `tools/artifact.py` | `jvagent/action/cockpit/tools/artifact.py` | `fbb5136` |
| `tools/clock.py` | `jvagent/action/cockpit/tools/clock.py` | `487f88e` |
| `tools/conversation.py` | `jvagent/action/cockpit/tools/conversation.py` | `fbb5136` |
| `tools/identity.py` | `jvagent/action/cockpit/tools/identity.py` | `addd1c2` |
| `tools/memory.py` | `jvagent/action/cockpit/tools/memory.py` | `7743650` |
| `tools/response.py` | `jvagent/action/cockpit/tools/response.py` | `d10a5b5` |
| `tools/search.py` | `jvagent/action/cockpit/tools/search.py` | `fbb5136` |
| `tools/skill.py` | `jvagent/action/cockpit/tools/skill.py` | `4bc6db6` |
| `tools/task.py` | `jvagent/action/cockpit/tools/task.py` | `a936aa1` |
| `registry/__init__.py` | `jvagent/action/cockpit/registry/__init__.py` | `fbb5136` |
| `registry/access.py` | `jvagent/action/cockpit/registry/access.py` | `a936aa1` |
| `registry/assembler.py` | `jvagent/action/cockpit/registry/assembler.py` | `4bc6db6` (replaces C-2 stub) |
| `registry/shim.py` | `jvagent/action/cockpit/registry/shim.py` | `fbb5136` |
| `catalog/__init__.py` | `jvagent/action/cockpit/catalog/__init__.py` | `fbb5136` |
| `catalog/action_resolver.py` | `jvagent/action/cockpit/catalog/action_resolver.py` | `fbb5136` |
| `catalog/prompts.py` | `jvagent/action/cockpit/catalog/prompts.py` | `acc351e` |
| `catalog/skill_catalog.py` | `jvagent/action/cockpit/catalog/skill_catalog.py` | `9310ef9` |
| `catalog/skill_discovery.py` | `jvagent/action/cockpit/catalog/skill_discovery.py` | `fbb5136` |
| `routing/__init__.py` | `jvagent/action/cockpit/routing/__init__.py` | `fbb5136` |
| `routing/preclassifier.py` | `jvagent/action/cockpit/routing/preclassifier.py` | `9ebd1de` |
| `routing/prompts.py` | `jvagent/action/cockpit/routing/prompts.py` | `fcb4d82` |
| `routing/router.py` | `jvagent/action/cockpit/routing/router.py` | `fcb4d82` |
| `routing/types.py` | `jvagent/action/cockpit/routing/types.py` | `9ebd1de` |
| `delivery/__init__.py` | `jvagent/action/cockpit/delivery/__init__.py` | `fbb5136` |
| `delivery/delegation.py` | `jvagent/action/cockpit/delivery/delegation.py` | `fbb5136` |
| `delivery/gates.py` | `jvagent/action/cockpit/delivery/gates.py` | `c60c043` |
| `delivery/helpers.py` | `jvagent/action/cockpit/delivery/helpers.py` | `c60c043` |
| `delivery/persona_delivery.py` | `jvagent/action/cockpit/delivery/persona_delivery.py` | `d10a5b5` |

### C-1 (skeleton, not duplicated)

`reasoning_helm.py`, `__init__.py`, `endpoints.py`, `info.yaml` —
authored fresh; reference `jvagent/action/cockpit/cockpit_interact_action.py`
at `3cd4ebb` for the orchestration pattern they mirror.
