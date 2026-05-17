# jvagent/action/cockpit/ — Agent Guide

> Local guide for the Cockpit subsystem. Cross-link: [`/docs/COCKPIT.md`](../../../docs/COCKPIT.md) (user-facing reference), [`/.planning/SPEC.md`](../../../.planning/SPEC.md) §3.3, [`/.planning/adr/0002-walker-revisit-cockpit.md`](../../../.planning/adr/0002-walker-revisit-cockpit.md).

---

## 1. What this directory owns

The model-cockpit pattern: walker-revisit driven model loop with full tool agency.

```
CockpitInteractAction.execute(visitor)
  └─ Phase 1: CockpitRouter         (lightweight LLM classifier)
  └─ Phase 2: CockpitEngine.step()  (one model call per visit)
        ├─ tool_calls? dispatch + visitor.prepend([self]) → next visit
        └─ text only? deliver final response → done
```

---

## 2. Key files

| File | Purpose |
|---|---|
| `cockpit_interact_action.py:79` | `CockpitInteractAction` class (weight=-200 by default) |
| `cockpit_interact_action.py:91-199` | All tunable attributes (model, iterations, timeouts, thresholds, prompts) |
| `engine.py` | `CockpitEngine` — initialize() + step() |
| `session.py` | `CockpitSession` + state plumbing (lives on `visitor._skill_state`) |
| `context.py` | `CockpitContext` — per-run data envelope |
| `config.py` | `CockpitConfig` — derived runtime config |
| `contracts.py` | `TerminationReason`, posture enums, contract types |
| `routing/router.py` | `CockpitRouter` (Phase 1) |
| `routing/preclassifier.py` | Cheap local heuristic for smalltalk/pleasantries (skips LLM round-trip when unambiguous) |
| `routing/types.py` | `RoutingResult`, posture constants (`POSTURE_RESPOND` / `SUPPRESS` / `DEFER`) |
| `prompts.py` | Default system / planning / security / citation prompt templates |
| `catalog/skill_catalog.py` | Skill discovery + filtering |
| `catalog/skill_discovery.py` | Skill index helpers |
| `delivery/` | Delegation, gates, helpers, persona delivery |
| `registry/access.py` | Access-control filters for routed skills + interact actions |
| `registry/shim.py` | `CockpitVisitorShim` — walker proxy for engine code |
| `memory_tools.py`, `response_tools.py`, `task_tools.py`, `conversation_tools.py`, `skill_tools.py`, `artifact_tools.py`, `search_tools.py` | Harness service tools (always available to the model) |
| `tools/` | Tool wiring + registry |

---

## 3. The walker-revisit pattern (memorize)

```python
# Pseudocode of execute()
async def execute(self, visitor):
    state = self._get_or_init_state(visitor)
    result = await self.engine.step(state, visitor)
    if result.has_tool_calls:
        await self._dispatch(result.tool_calls)
        visitor._skill_state = state            # persist for next visit
        await visitor.prepend([self])            # ← KEY: re-enqueue self
        return
    # Text response — finalize
    await self._deliver(result.text, visitor)
```

Each walker visit = exactly one model call. Streaming flush, action recording, access control checks happen *between* visits, not inside an inner loop. See [`/.planning/adr/0002-walker-revisit-cockpit.md`](../../../.planning/adr/0002-walker-revisit-cockpit.md).

---

## 4. Termination conditions

`engine.py:188-219` checks them in this order each step:

1. **`max_duration_seconds`** elapsed (default `300.0`, [`engine.py:188`](engine.py)).
2. **`max_iterations`** reached (default `25`, [`engine.py:203`](engine.py) — also exposed via [`cockpit_interact_action.py:105`](cockpit_interact_action.py)).
3. **Stuck detection**: Jaccard similarity over the last `stuck_detection_window` tool calls ≥ `stuck_intent_jaccard_threshold` (defaults 4 / 0.65, [`cockpit_interact_action.py:187-190`](cockpit_interact_action.py)). Also: a single primary tool repeated `stuck_primary_tool_repeat` times.
4. **Text response** with no tool calls → deliver, exit.
5. **Degenerate response**: if the final text is < `degenerate_response_max_chars` characters (default 25), the framework may retry or fall back.

Note: there is **no** runtime check that operator-set `max_iterations` is below jvspatial's walker cap `max_visits_per_node=100`. If you raise `max_iterations` above 100, the walker's per-node visit limit will trip first.

---

## 5. Tool registry shape

The cockpit assembles three tool groups per run:

| Group | Source | Naming |
|---|---|---|
| Harness service tools | `cockpit/*_tools.py` | `memory_*`, `response_*`, `task_*`, `conversation_*`, `skill_*`, `artifact_*`, `cockpit_search` |
| Action tools | `Action.get_tools()` from each enabled action | `action__{action_name}__{tool}` |
| Skill tools | Skill scripts via `SkillCatalog` | `{safe_skill_name}__{tool_name}` (hyphens in skill names become underscores in `safe_skill_name` per [`assembler.py:337`](assembler.py)) |

Tool tiers (`cockpit_interact_action.py:158`):
- `minimal` — bare essentials.
- `standard` (default) — most harness tools.
- `full` — everything including search + artifacts.

**`block_raw_tool_invocation` is prompt-level only.** Setting it to True appends a security block to the system prompt instructing the model not to honor user-typed `/skill` or `tool_name(args)` patterns. It does NOT structurally sanitize `interaction.utterance` or filter tool dispatch — a jailbroken model can still be talked into calls. For real enforcement, sanitize the utterance upstream (channel adapter) or use `AccessControlAction`. AUDIT-interact DRIFT-08.

---

## 6. Configurable surfaces (most-tuned)

| Attribute | Default | Purpose |
|---|---|---|
| `model` | `claude-sonnet-4-20250514` | Main engine model |
| `model_action_type` | `AnthropicLanguageModelAction` | Which LM action to bind |
| `router_model` | `gpt-4o-mini` | Phase 1 classifier model |
| `max_iterations` | 25 | Hard step cap |
| `max_duration_seconds` | 300.0 | Wall-clock cap |
| `model_temperature` | 0.3 | |
| `model_max_tokens` | 8192 | |
| `reasoning_effort` | medium | |
| `conversational_fast_path` | true | Skip engine for trivial smalltalk routes |
| `stream_internal_progress` | true | Stream thoughts + tool-progress badges |
| `enable_router_preclassifier` | true | Local heuristic before router LLM |
| `enable_interact_router_cache` | false | In-process router cache (45s TTL) |
| `preload_user_memory` | true | Prepend user memory to system prompt |
| `auto_track_tasks` | true | Cockpit creates/updates Task nodes |
| `plan_first` | true | Force task plan before action |
| `tool_tier` | standard | minimal / standard / full |
| `system_prompt` / `task_planning_prompt` / `security_prompt` / `citation_instruction` | "" | Override empty → use `prompts.py` defaults |

Override via `agent.yaml`:
```yaml
- action: jvagent/cockpit
  context:
    model: claude-opus-4-7
    max_iterations: 40
    tool_tier: full
```

---

## 7. Tests

- `tests/action/cockpit/` — engine, routing, state, stuck detection, tool dispatch.
- `tests/test_tool_schema_audit.py` — sanity of tool JSON schemas.

```bash
pytest tests/action/cockpit/ -v
```

---

## 8. Traps specific to cockpit/

| Trap | Fix |
|---|---|
| Adding state inside `engine.step()` not persisted on `_skill_state` | Lost between visits. Add to `CockpitSession`. |
| Calling the model twice per visit | Defeats walker-revisit; bypasses per-step access control. One call per `step()`. |
| Using `visitor.visit([self])` (tail) instead of `visitor.prepend([self])` | Other queued nodes run between cockpit visits; surprises user. |
| Editing prompts in `cockpit_interact_action.py` instead of `prompts.py` | Overrides are blank by default. Default text lives in `prompts.py`. |
| Adding a harness tool without registering its access-control rule | Anyone with engine access can call it. Add a check in `registry/access.py`. |
| Forgetting `stuck_min_iterations` threshold | Stuck detection trips on early visits. Set ≥ 4. |
| Action tools not prefixed `action__` | Cockpit registry skips them. |
| Hardcoding tool descriptions | Models read these; quality affects routing. Keep clear and short. |

---

## 9. Don't touch from outside cockpit/

- Walker-revisit semantics — `engine.step()` + `visitor.prepend([self])` is load-bearing.
- Termination contracts — they bound cost and latency for every cockpit-driven turn.
- Tool naming prefixes — collision rules depend on them.

---

## 10. Out of scope here

- Walker mechanics (visit/prepend/spawn): see `jvspatial`'s walker and [`/.planning/jvspatial-integration.md`](../../../.planning/jvspatial-integration.md).
- Skill authoring: separate doc set (`jvagent/skills/`).
- PersonaAction prompt shape: see `jvagent/action/persona/`.
