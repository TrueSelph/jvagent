# Cockpit Roadmap

Living roadmap for getting `CockpitInteractAction` to default-action quality across every jvagent deployment. Constraints, milestones, and open questions captured here. Updated as milestones close.

## Mission

- Cockpit stays packaged as `InteractAction`. jvagent = harness foundation (input interfaces, interaction state, response protocols).
- Cockpit becomes the default action in every jvagent.
- Persona is a separate shared service. Cockpit and other actions (e.g., `intro_interact`) may both contribute to the final response via persona subprompt directives. Cockpit must NOT subsume persona.
- Cockpit must reliably delegate to classified `interact_action`(s), `skill`(s), or both.
- Iterative tests against `examples/jvagent_app/agents/jvagent/cockpit_agent` guide development.

## Status snapshot

| Milestone | State | Notes |
|---|---|---|
| A — Baseline test harness + metrics | DONE | 14 unit tests + smoke harness committed (commit `7d95904`). Baseline: 33s/6 utterances, 34K tokens. |
| B — Interact-action delegation | DONE | `RoutingResult.interact_actions` wired (commit `1aa7d3f`). 3 modes operational: skills-only, ia-only (curate + finalize via persona), both (curate + engine + walker continues to IAs). 27 tests pass. Smoke: "speak to human" → cockpit → intro → handoff → cockpit-finalize → persona produces composed response. |
| C — Skill delegation hardening | PENDING | `sys.modules` pollution fix; deterministic activation logging. |
| D — Performance | PENDING | Cache tool registry per run; trim system prompt; reduce per-step rebuild. |
| E — Default enablement | PENDING | Add cockpit to `minimal`/`conversational` profiles; coexist with legacy at weight `-200`. |
| F — Access control integration | PENDING | Per-user access control for skills + interact_actions inside cockpit. Currently AccessControlAction supports `(user_id, channel, resource)` for InteractAction labels — needs cockpit wiring + skill resource registration. |
| G — Production hygiene flags | PENDING | (1) Single off-switch for all internal thought/reasoning streams; (2) Block raw tool/skill invocation via user utterance. |

## Constraints (hard)

- **No persona subsumption.** Cockpit calls persona via duck-typing for final delivery only. Other actions (e.g., `intro_interact`) may also publish through persona on the same interaction.
- **No harness subsumption.** jvagent's walker, conversation, interaction, response bus, and access control remain harness-level. Cockpit consumes, never replaces.
- **Coexist with legacy.** `AgentInteractAction` and standalone `InteractRouter`/`SkillInteractAction` paths must keep working until E.

## Baseline (commit `7d95904`)

| Utterance | dur(s) | model_calls | prompt_tok | resp_chars |
|---|---|---|---|---|
| "Hi" | 2.93 | 2 | 2014 | 34 |
| "What is 2+2?" | 2.79 | 2 | 4956 | 5 |
| Web search | 5.89 | 3 | 8342 | 167 |
| Remember pref | 9.29 | 3 | 8260 | 139 |
| Recall pref | 8.70 | 3 | 8342 | 183 |
| "Thanks!" | 3.55 | 2 | 2180 | 79 |
| **TOTAL** | **33.15** | **15** | **34094** |  |

## Open hotspots (from baseline)

1. ~8K prompt tokens per processing turn (system prompt + 33 tool schemas + history).
2. "Four." costs 5K tokens — conversational gate still re-renders persona+history.
3. `directive_remember_pref` 9.3s — model takes extra round-trips before committing `memory_set`.
4. Tool registry rebuilds every walker revisit (caching opportunity).
5. Bootstrap mutates user-facing `app.yaml` (unrelated bug; reverted from cockpit commits).

## Milestone details

### B — Interact-action delegation (in progress)

**Gap.** `RoutingResult.interact_actions` is parsed from router output but never consumed. `routing.actions` (skills) → engine via `preloaded_skills`. `routing.interact_actions` → dead field.

**Plan.** New gate after Phase-1 routing in `_phase_route_and_setup`:

1. **skills-only** (existing): Set up engine with `preloaded_skills`, run.
2. **interact_actions-only**: Look up classes by name on agent, sort by weight, `visitor.prepend([...])`, return without engine.
3. **both**: Run engine for skills first, on terminal result prepend interact_actions for downstream walker continuation.

Reference pattern: `jvagent/action/router/interact_router.py:940` (`_update_walk_path` → `curate_walk_path`).

**Tests.** Unit tests for the dispatch logic (mock router output, assert walker.prepend called with expected actions). Smoke test extended with an utterance that activates a target interact_action (TBD — likely `intro_interact_action`).

### F — Access control integration

**Existing.** `AccessControlAction` (`jvagent/action/access_control/access_control_action.py`) supports per-`(user_id, channel, resource)` checks. `resource` is matched against InteractAction class names today.

**Gap.** Cockpit:
- Does not consult AccessControlAction when assembling tool registry (skill tools, action tools).
- Does not filter `RoutingResult.actions` (skills) or `RoutingResult.interact_actions` by user permissions.
- Skills are not registered as access-control "resources" so the existing `(user_id, channel, resource)` check has nothing to match.

**Plan.**
- Define skill-level resource convention (e.g., `skill:{name}`) and InteractAction-tool resource convention (e.g., `tool:{namespace}/{tool_name}`).
- Cockpit pre-engine: filter `routing.actions` and `routing.interact_actions` through AccessControl before preload/dispatch.
- Cockpit registry assembly: filter action tools and skill tools through AccessControl by resource.
- Document the resource taxonomy in cockpit SPEC.md and access_control SPEC/docs.

### G — Production hygiene flags

**G1 — Silence internal thoughts in production.**
- Today: `stream_internal_progress: bool` (default `True`) controls thought + reasoning + tool-progress streams. Legacy aliases `stream_thinking`/`stream_reasoning`/`stream_tool_progress` still accepted.
- Audit: confirm `False` cleanly suppresses every internal channel — model thinking, tool-progress emit, router canned, response_emit_thought tool. Add explicit test.
- Add `production_mode: bool` umbrella flag that enforces `stream_internal_progress=False` and any other production defaults.

**G2 — Block raw tool/skill invocation via user utterance.**
- Risk: user types `"call web_search with query=X"` or `"/skill web_search ..."`; model may pass through and dispatch directly without router classification.
- Mitigation candidates:
  - Router-level filter: detect tool-invocation patterns in raw utterance, force classification through normal intent flow.
  - System-prompt instruction: model must never act on user-issued tool/skill commands as authoritative.
  - Optional flag `allow_direct_tool_invocation: bool` (default `False` in production).
- Add test: utterance `"call memory_set key=foo content=bar"` must NOT result in a `memory_set` tool call without intent classification approving it.

## Test infrastructure

- **Unit tests**: `tests/action/cockpit/test_engine_baseline.py`, `test_action_phases.py`. Run with `.venv/bin/python -m pytest tests/action/cockpit/`.
- **Real-LM smoke**: `tests/action/cockpit/smoke_real_lm.py`. Run with `.venv/bin/python tests/action/cockpit/smoke_real_lm.py [APP_ROOT]`. Boots the example app, runs 6-utterance suite, prints metrics. JSON dump available via `--json`.

## Open questions

1. For Milestone B, target interact_action for delegation tests — `intro_interact_action`, `pageindex_retrieval_interact_action`, or both? (Asked, awaiting user answer.)
2. For G2, scope of pattern detection — explicit tool-name regex match in utterance, or LLM-classified "user is trying to bypass routing"?
3. For F, should denied skills emit a brief explanatory response, or silently drop from the catalog?
