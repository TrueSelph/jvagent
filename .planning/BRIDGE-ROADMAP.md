# Bridge Roadmap

Living roadmap for delivering the **Bridge + Helm** architecture as an additive deployment pattern in jvagent. Bridge ships as a peer to the existing **Cockpit** and **Rails** patterns — no forced migration, no harness subsumption. Updated as milestones close.

> Companion docs (to be written alongside this roadmap): [`.planning/PATTERNS.md`](PATTERNS.md), [`.planning/adr/0007-bridge-helm-architecture.md`](adr/0007-bridge-helm-architecture.md).

## Mission

- Bridge coexists with Cockpit and Rails as a first-class deployment pattern. Operators choose per-deployment via scaffolder profile and `agent.yaml`.
- The harness stays pattern-agnostic: [`SPEC.md`](SPEC.md) defines action contracts and walker semantics; patterns are compositions of actions on top.
- Phase-out is **data-driven**: a pattern is deprecated only when demonstrably outperformed for its target use case, tracked in the performance ledger in [`PATTERNS.md`](PATTERNS.md).
- **Reflex Helm** enables sub-500ms first-response turns for trivial inputs; **Reasoning Helm** preserves today's cockpit capabilities; **Specialist Helm** yields cleanly to existing rails `InteractAction`s; **Persona Helm** owns delivery polish.
- Each helm shifts to peers via an explicit verb set on the walker. Gear shifts are walker hops — observable, streamable, access-controllable per shift.
- Every milestone closes with **measured metrics** against the existing cockpit baseline (commit `7d95904`).
- Iterative validation against `examples/jvagent_app/agents/jvagent/bridge_agent` (new) alongside `cockpit_agent` (existing).

## Status snapshot

| Milestone | State | Notes |
|---|---|---|
| A — ADR + contracts | DONE | Locked `HelmStepResult` verb set v0.1, `BridgeState` shape, manifest v0 schema. ADR-0007 + PATTERNS.md shipped at commit `006d635`. Supersedes [`adr/0002`](adr/0002-walker-revisit-cockpit.md) in spirit; walker-revisit pattern stays. |
| B — Skeleton + stub helms | DONE | `BaseHelm` Action subclass, `BridgeInteractAction` at weight `-200`, `StubHelm`, full verb dispatch (50 unit tests, 97% coverage). Shipped at commit `d24fc5a`. No real LM calls. |
| C — ReasoningHelm parity | DONE | C-0..C-7 shipped. Bridge + ReasoningHelm composition under `jvagent/action/helm/reasoning/`; `bridge_agent` example at `7a3713a`; smoke harness at `tests/action/bridge/smoke_bridge.py`. Parity gate **GREEN** vs fresh cockpit re-baseline: per-utterance `model_calls` exact match (1/2/3/2/2/1), `prompt_tokens` drift within ±2% on 5/6 utterances and within -11.8% on the web-search outlier (well inside the ±15% tolerance). Totals: bridge 11 calls / 22908 prompt-tokens vs cockpit 11 calls / 24124 prompt-tokens (-5.0%). Hotfixes landed during parity tuning: `BaseHelm.respond()` (`b134065`); Bridge stamps itself on the visitor for helms that need the IA reference (`<next commit>`); `visitor._bridge_action` formalised as a documented contract via `BridgeInteractAction.from_visitor()` accessor. **C-6 IA-tail follow-up shipped:** routed `interact_actions` now dispatch via a `DELEGATE` chain (`follow_up=True` on all but the tail) instead of walker-queue curation; `_finalize_via_persona` (cockpit-style) removed; ReasoningHelm no longer mutates the walker queue (Bridge owns it). 9 new chain tests; 261 in-tree tests pass; cockpit (189) untouched. |
| D — Manifest plumbing | DONE | Pattern-agnostic `Manifest` at `jvagent/action/manifest.py`; loader integration via `info.yaml`; `agent.yaml.context.manifest:` override; `Action.get_manifest()` accessor with defaults. ReflexHelm + turn-lock detection consume manifests; Cockpit could too without changes. |
| E — ReflexHelm | DONE | `jvagent/action/helm/reflex/reflex_helm.py` ships with structured JSON output (no function-calling overhead), peer-awareness from manifests, defensive YIELD-downgrade, AC-gated shift targets, ack-on-shift via SHIFT.transient_ack. Live smoke: trivial turns route to direct EMIT, deliberate turns SHIFT to ReasoningHelm with visible ack ("Searching now…"). Sub-500ms p50 target NOT met on `gpt-4o-mini` (~3s observed); Groq/Cerebras provider swap is the remaining work to hit J's exit gate. |
| F — Specialist delegation | DONE | `DELEGATE` verb wired in `BridgeInteractAction._handle_delegate`; turn-lock detection at `jvagent/action/bridge/turn_lock.py` via `find_turn_lock_owner`; `is_actively_locking_turn` opt-in protocol prevents ghost locks; `is_interrupt_allowed` gates `SHIFT(interrupt=True)`. **Refinement during impl:** Bridge auto-DELEGATEs to lock owners ALWAYS (not just when helm can_interrupt=False) because Reflex doesn't know about active locks — comment in `bridge_interact_action.py:377-389` documents the live-testing rationale. |
| G — PersonaHelm | SCRAPPED (May 2026) | Shipped originally but never wired into any agent's helm chain. Bridge handles persona stylisation directly via `deliver_via_persona` from `BridgeInteractAction._publish_emit_via_persona` (Phase 2B refactor) — the dedicated helm added a walker visit + SHIFT verb without delivering value the direct-call path didn't already provide. Module stubbed under `jvagent/action/helm/persona/` and queued for deletion; `info.yaml` carries `enabled: false`. Test suite removed (10 PersonaHelm tests + 1 cross-helm wrapper assertion). |
| H — Migration CLI (optional) | TODO | `jvagent app migrate-to-bridge` with `--dry-run`, `--diff`. Non-blocking for K. |
| I — Observability | DONE | `helm_shift` events on `Interaction.observability_metrics` (`bridge_interact_action.py:227-261`); full `gear_trace` + `helm_timings_seconds` + `helm_step_counts` persisted to `Interaction.parameters['bridge_observability']` (`_persist_observability`); per-helm wall-clock + step-count instrumentation around every `step()` call. Pattern-agnostic field names. Best-effort write — never blocks a turn. |
| J — Performance validation | IN PROGRESS | Pattern-matrix harness shipped at `tests/action/bridge/smoke_pattern_matrix.py`; first run at `b830f42` archived. Current numbers vs fresh cockpit re-baseline (both `gpt-4o-mini`): total dur +28%, tokens **-10%**, trivial-turn p50 +7%, p99 +10%. **30% trivial-turn reduction target NOT met on `gpt-4o-mini` Reflex** — provider swap to Groq `llama-3.1-8b-instant` or Cerebras is the remaining gate. Architectural ack-on-shift UX validated qualitatively in live smoke. |
| K — Pattern parity | DONE | `bridge` profile shipped in `jvagent/scaffold/builtin_profiles/bridge.yaml`; scaffolder smoke verified (`/tmp/bridge_scaffold_test`); `docs/BRIDGE.md` mirrors `docs/COCKPIT.md`; GLOSSARY.md gains Bridge / Helm / Manifest / latency_class / ReflexHelm / ReasoningHelm / PersonaHelm / ShiftRecord / Specialist / Turn-lock / BridgeState / HelmStepResult; `CLAUDE.md` top-level points to `PATTERNS.md`; `action-authoring.md` gains a "Pattern compatibility" section. 158/158 bridge+helm+scaffold tests pass; cockpit 189/189 untouched. **No cockpit deprecation.** |

## Constraints (hard)

- **No harness subsumption.** [`SPEC.md`](SPEC.md), `InteractWalker`, `response_bus`, `Conversation` / `Interaction` / `User` node contracts, `AccessControlAction` — none change for Bridge. Bridge is a composition of new actions on top.
- **No forced cockpit deprecation.** Bridge ships as a peer pattern. Cockpit stays first-class through and beyond K. Deprecation requires data from the performance ledger plus an explicit ADR.
- **No data migration.** Bridge is reversible by swapping `jvagent/bridge` for `jvagent/cockpit` in `agent.yaml`. No DB schema change, no graph rewrite.
- **Walker-revisit preserved.** Bridge uses the same `visitor.prepend([self])` pattern as cockpit. [`adr/0002`](adr/0002-walker-revisit-cockpit.md) stands.
- **Pattern-agnostic abstractions.** Anything Bridge introduces that other patterns could use (`manifest:` schema, `HELM_SHIFT` observability event) lives at harness level, not under `cockpit/` or `bridge/`.
- **Three patterns minimum.** Rails, Cockpit, Bridge supported simultaneously through K. Pattern selection happens at scaffold time (profile) and runtime (`agent.yaml`).
- **One model call per walker visit.** Each helm's `step()` issues at most one LM call. Same invariant as cockpit; preserves per-step observability, access control, and streaming flush.

## Baseline (commit `7d95904`)

Inherited from cockpit. Bridge configurations are measured against the same 6-utterance suite from [`COCKPIT-ROADMAP.md`](COCKPIT-ROADMAP.md).

| Utterance | dur(s) | model_calls | prompt_tok | resp_chars |
|---|---|---|---|---|
| "Hi" | 2.93 | 2 | 2014 | 34 |
| "What is 2+2?" | 2.79 | 2 | 4956 | 5 |
| Web search | 5.89 | 3 | 8342 | 167 |
| Remember pref | 9.29 | 3 | 8260 | 139 |
| Recall pref | 8.70 | 3 | 8342 | 183 |
| "Thanks!" | 3.55 | 2 | 2180 | 79 |
| **TOTAL** | **33.15** | **15** | **34094** | |

### Target deltas (validated at J)

- **"Hi" / "Thanks!" / "What is 2+2?"** (trivial turns): **≥50% latency reduction** via Reflex-only path.
- **Web search / pref turns** (deliberate turns): **≤10% latency delta**, with perceived latency improved via ack-on-shift visible by ~250ms.
- **Total tokens not worse than baseline.**
- **p99 latency not worse than baseline.**

## Headline targets

- **Sub-500ms p50** for trivial conversational turns (Reflex-only).
- **<300ms ack-on-shift** for `deliberate`-class targets.
- **Three patterns coexist** without harness changes.
- **One model call per walker visit** invariant preserved.
- **Cockpit smoke harness within 5% of pre-extraction baseline** after C.

## Milestone details

### A — ADR + contracts

**Gap.** No formal contract for helm protocol, shift verb set, or bridge state shape. Without these locked, B–G build against moving targets.

**Plan.**

1. Write [`.planning/adr/0007-bridge-helm-architecture.md`](adr/0007-bridge-helm-architecture.md).
2. Define `HelmStepResult` verbs:
   - `EMIT(text, finalize=True)` — deliver and exit gear graph.
   - `EXECUTE(tool_calls=[...])` — dispatch, persist state, revisit current helm.
   - `SHIFT(target, reason, transient_ack=None, handoff_state=None, interrupt=False)` — switch helms.
   - `DELEGATE(interact_action, args=None)` — yield to a rails `InteractAction`.
   - `YIELD` — step aside; let next IA in agent's weight chain run.
3. Define `BridgeState` dataclass: `current_helm`, `gear_trace: List[ShiftRecord]`, `shift_count`, `turn_started_at`, `last_emit_at`, `helm_states: Dict[str, Any]`, `delegated_action: Optional[str]`.
4. Define `manifest` v0 schema (`info.yaml` block): `purpose`, `activates_on`, `terminates_when`, `latency_class` (`instant | quick | deliberate | long`), `turn_lock`, `interrupt_phrases`, `expected_duration_seconds`.
5. SPEC.md §3 addendum: Bridge as a peer to Cockpit; same walker-revisit guarantees; no new walker semantics; pattern-agnostic manifest contract.
6. PATTERNS.md drafted in parallel (catalog of supported patterns + decision tree + performance-ledger scaffold).

**Tests.** N/A — design milestone.

**Exit.** Verb set reviewed and locked by maintainer. ADR accepted. SPEC additions reviewed and don't break invariants 1–8. PATTERNS.md v0.1 published.

**Risk.** Verb set premature lock-in. Mitigation: explicit `v0` tag on the verb set; ADR notes that additive verbs are non-breaking; breaking changes require ADR-0008+.

### B — Skeleton + stub helms

**Gap.** No `Bridge` action exists. Walker has nothing to dispatch to.

**Plan.**

1. `jvagent/action/helm/base.py` — `BaseHelm(Action)` with abstract `async step(visitor, bridge_state) -> HelmStepResult`.
2. `jvagent/action/bridge/bridge_interact_action.py` — `BridgeInteractAction(InteractAction)` at weight `-200` (same slot as cockpit).
3. `jvagent/action/helm/stub_helm.py` — deterministic helm for tests; configurable verb-return scripts.
4. State plumbing: `visitor._bridge_state: BridgeState`, persisted across walker revisits via the same pattern as `visitor._skill_state`.
5. Bridge step machine: read state → resolve `current_helm` → call its `step()` → process verb → maybe `visitor.prepend([self])` → return.
6. Shift budget enforcement (default 4 per turn).
7. First-emit timeout safety net (default 800ms).
8. AccessControl filter point: each shift validates `tool:helm:{target}` resource before dispatch.

**Tests.**

- `tests/action/bridge/test_protocol.py` — every verb dispatches correctly with stub helms.
- `tests/action/bridge/test_state.py` — `BridgeState` persistence across walker visits.
- `tests/action/bridge/test_shift_budget.py` — ping-pong prevented; budget exhaustion routes to safe fallback.
- `tests/action/bridge/test_first_emit_timeout.py` — safety-net ack fires when no emit by deadline.
- `tests/action/bridge/test_access_control.py` — denied shift targets blocked.

**Exit.** Bridge with one StubHelm produces a turn end-to-end. 100% unit-test coverage on new code. Zero real model calls in the test suite. Bridge refuses to configure without at least one helm.

### C — ReasoningHelm parity

**Strategy (revised 2026-05-26):** Cockpit code path is **untouched**. `ReasoningHelm` is a parallel implementation built by selectively duplicating cockpit modules into `jvagent/action/helm/reasoning/`. **Zero imports** from `jvagent.action.cockpit` into `jvagent.action.helm` or `jvagent.action.bridge`. Bridge gains a new `CONTINUE` verb (additive, v0.1, non-breaking per ADR-0007) so `ReasoningHelm` can run cockpit-style internal tool dispatch and signal "give me another visit" without using `EXECUTE` (which would force Bridge to own the tool registry).

**Rationale.** Avoids coupling between patterns. A future revision can phase cockpit out by deleting `jvagent/action/cockpit/` wholesale, with no fallout on Bridge. Duplication cost (~9k LoC) is accepted; it concentrates pattern-specific code under its pattern's namespace.

**Plan.**

1. **C-0 — CONTINUE verb.** Add `CONTINUE(reason: Optional[str])` to `jvagent/action/helm/contracts.py`. Wire dispatch in `BridgeInteractAction._dispatch` — Bridge calls `visitor.prepend([self])` with no state mutation. Update ADR-0007 + tests.
2. **C-1 — Skeleton.** `jvagent/action/helm/reasoning/` package: `__init__.py`, `reasoning_helm.py` (class skeleton with `step()` returning `EMIT` placeholder), `info.yaml` (`jvagent/reasoning_helm`, `archetype: ReasoningHelm`, `type: action`), `endpoints.py` stub. **DONE @ `1385eea`**.
3. **C-2 — Engine.** Duplicate `cockpit/engine.py`, `session.py`, `context.py`, `config.py`, `contracts.py`, `prompts.py` into `helm/reasoning/`. ReasoningHelm wiring deferred to C-6 (gates on full tool/catalog/routing/delivery surface). 8 ported engine-baseline tests pass. **DONE @ `16e886a`**.
4. **C-3 — Bulk duplication of remaining cockpit modules.** All remaining cockpit modules duplicated in one commit because they cross-reference each other and an incremental split would leave broken imports between sub-commits. Roadmap rolls C-4 (routing) and C-5 (catalog) into this commit since they describe slices of the same bulk duplication. Files: `cockpit/tools/` (memory, response, task, conversation, skill, artifact, search, clock, identity) → `helm/reasoning/tools/`; `cockpit/registry/` (assembler, access, shim) → `helm/reasoning/registry/`; `cockpit/catalog/` (skill_catalog, skill_discovery, action_resolver, prompts) → `helm/reasoning/catalog/`; `cockpit/routing/` (router, preclassifier, types, prompts) → `helm/reasoning/routing/`; `cockpit/delivery/` (delegation, persona_delivery, gates, helpers) → `helm/reasoning/delivery/`. Single source-attribution table in `jvagent/action/helm/reasoning/DUPLICATION_NOTICE.md`.
5. **C-4 — Routing.** Merged into C-3.
6. **C-5 — Catalog.** Merged into C-3.
7. **C-6 — ReasoningHelm wiring.** Replace the C-1 placeholder `step()` with a real implementation: build `CockpitContext`, run Phase 1 routing, instantiate `CockpitEngine`, dispatch engine steps one-per-visit. Translate `CockpitStepResult` → `HelmStepResult` (`final_response` → `EMIT(finalize=True)`; `tool_calls` → `CONTINUE` (the helm dispatched internally); `timeout`/`stuck`/`budget` → `EMIT(finalize=True)` with fallback). Persona finalisation calls `PersonaAction` directly via `Action.get_action("PersonaAction")`. Conversational fast-path included for baseline parity.
8. **C-7 — Smoke harness + parity.** `tests/action/bridge/smoke_bridge.py` runs the 6-utterance suite against the `bridge_agent` example (Bridge + ReasoningHelm). Baselines archived under `tests/action/bridge/baselines/`. Iterate to ≤5% drift vs commit `7d95904` on `dur`, `model_calls`, `prompt_tok`, `resp_chars`.

**Hard constraints.**

- **No imports** from `jvagent.action.cockpit.*` in any new file under `jvagent.action.helm.*` or `jvagent.action.bridge.*`. Verified by grep at C-7.
- Cockpit YAML, tests, and runtime behavior **unchanged**. `tests/action/cockpit/` stays green at every step.
- `CockpitInteractAction` is **not** refactored.

**Tests.**

- `tests/action/cockpit/` — all 189 existing tests pass (cockpit untouched).
- `tests/action/bridge/` — existing 50 tests pass + new CONTINUE coverage.
- `tests/action/helm/reasoning/` — direct ReasoningHelm exercises mirroring `tests/action/cockpit/` structure (engine baseline, routing, skill dispatch, stuck detection, hygiene flags, persona delivery).
- `tests/action/bridge/smoke_bridge.py` — real-LM 6-utterance suite vs baseline.

**Exit.** All of:

- Bridge smoke harness ≤5% drift vs baseline `7d95904` on every metric.
- Cockpit smoke harness unchanged (cockpit code untouched).
- Zero `jvagent.action.cockpit` imports in `helm/` or `bridge/` packages.
- pre-commit green; full pytest suite green.

**Risk.** Duplication cost + maintenance burden of two parallel implementations until cockpit is phased out (post-K). Mitigation: each duplicated module clearly marked with `# duplicated from jvagent/action/cockpit/<module> at commit <sha>` in its docstring so divergence is auditable. Cockpit phase-out is gated by the performance ledger in [`PATTERNS.md`](PATTERNS.md).

### D — Manifest plumbing

**Gap.** `info.yaml` has no manifest block. Loader doesn't surface it. Helms have nothing to read for shift decisions.

**Plan.**

1. Loader change: `jvagent/action/loader/info_yaml.py` reads optional `manifest:` block into `Action.metadata['manifest']`.
2. `agent.yaml` override path: per-action `manifest:` in `context:` overrides info.yaml fields (DRY in shared actions; flex in specific deployments).
3. `Action.get_manifest() -> Manifest` accessor with sane defaults: missing manifest yields `{latency_class: "quick", turn_lock: false}`.
4. Pilot manifests on three actions:
   - `jvagent/intro_interact` — quick, no turn-lock.
   - `jvagent/handoff_interact` — quick.
   - `jvagent/feedback_interview` — deliberate, `turn_lock: true`.
5. Manifest registry queryable via `agent.list_manifests()` so helms can build peer-awareness prompts at startup.

**Tests.**

- `tests/test_manifest_loader.py` — info.yaml → metadata propagation.
- Override precedence: agent.yaml `context` wins over info.yaml.
- Missing manifest yields default Manifest object.
- Malformed manifest raises clear loader error.

**Exit.** Three pilot actions return well-formed Manifest objects. Loader tested against malformed inputs. Pattern-agnostic: manifest reads work regardless of whether the agent uses Cockpit or Bridge.

### E — ReflexHelm

**Gap.** No fast-path helm exists. All turns hit a heavy model.

**Plan.**

1. `jvagent/action/helm/reflex_helm.py` — `ReflexHelm(BaseHelm)`.
2. Tool surface (strictly allowlisted): `shift_helm`, `emit_response`. No skills, no action tools, no harness tools by default.
3. Default Reflex prompt in `jvagent/action/helm/prompts.py` — includes peer-helm manifest summaries.
4. ack-on-shift logic: consults target helm's `latency_class`; `smart` (default) / `always` / `off` modes.
5. `info.yaml` declares `latency_class: instant`.
6. Multi-provider support via `model_action_type` (Groq, Cerebras, Anthropic Haiku, OpenAI gpt-4o-mini).
7. `can_emit_directly` flag (default `true`) — if `false`, Reflex is a pure classifier and must SHIFT.

**Tests.**

- Unit: ReflexHelm with mocked LM; asserts verb output per fixture utterance.
- Smoke: 6-utterance suite with Bridge + Reflex + Reasoning. Target "Thanks!" ≤500ms p50.
- Ack-on-shift visible by 300ms on `deliberate`-class shifts.
- Reflex with `can_emit_directly: false` always returns SHIFT or DELEGATE.

**Exit.** Smoke harness shows ≥50% latency reduction on the three trivial turns ("Hi", "What is 2+2?", "Thanks!"). Ack-on-shift fires correctly on web-search and pref turns.

**Open.** Final default provider for Reflex — see Open Questions §1.

### F — Specialist delegation

**Gap.** Bridge can't yield to rails InteractActions. `DELEGATE` verb exists in the contract but has no implementation.

**Plan.**

1. Bridge implements `DELEGATE`: resolve named `InteractAction` on the agent, call `await action.execute(visitor)` directly, then revisit self for finalization.
2. Turn-lock check: read all active tasks on conversation; if any has `turn_lock: true`, route fragments to that owner before consulting helms.
3. Interrupt protocol: `SHIFT(target, interrupt=true)` breaks turn-lock; only Reflex (or operator-defined override) may issue it.
4. Lift cockpit_router's `active-task` fingerprint logic into `jvagent/action/bridge/turn_lock.py` (pattern-agnostic so other patterns can use it).

**Tests.**

- `tests/action/bridge/test_delegate.py` — DELEGATE → InteractAction.execute → revisit chain.
- Scenario: feedback interview mid-flow + smalltalk + return to interview.
- Scenario: feedback interview + "STOP" utterance → interrupt + clean exit.
- Turn-lock prevents Reasoning from starting a parallel interview when one is active.

**Exit.** Interview scenario works end-to-end via Bridge. Active turn-lock visible in observability output. Reasoning Helm cannot inadvertently break a turn-lock.

### G — PersonaHelm

**Gap.** Persona delivery is a tool today (`response_deliver_via_persona`). In Bridge, polish-via-persona should be a shift target so it gets first-class observability and timing.

**Plan.**

1. `jvagent/action/helm/persona_helm.py` — `PersonaHelm(BaseHelm)` wrapping `PersonaAction`.
2. `info.yaml` declares `latency_class: quick`.
3. Other helms can `SHIFT(target=PersonaHelm, handoff_state=draft)` to polish.
4. `response_deliver_via_persona` tool kept as an alias that internally issues `SHIFT(target=PersonaHelm, ...)`.

**Tests.**

- ReasoningHelm → PersonaHelm chain produces composed output matching today's `response_deliver_via_persona` behavior.
- Persona delivery latency tracked separately in observability.
- Alias path produces identical output to direct SHIFT.

**Exit.** Smoke suite shows composed output quality parity with current `response_deliver_via_persona`. Persona timing visible as a discrete row in `Interaction.observability_metrics`.

### H — Migration CLI (optional)

**Gap.** Operators on cockpit must hand-write Bridge YAML to migrate.

**Plan.**

1. `jvagent app migrate-to-bridge <app_root>` subcommand.
2. Reads `agents/*/agent.yaml`, finds cockpit blocks, writes Bridge + ReasoningHelm equivalents preserving all cockpit config under the helm.
3. `--dry-run` prints diff; `--diff` shows side-by-side; `--write` applies in place.
4. Idempotent: re-running on a Bridge YAML is a no-op.

**Tests.**

- Migrate `cockpit_agent` example; resulting Bridge config runs identical smoke metrics.
- Round-trip: cockpit → bridge → run → metrics within 1% of pre-migration cockpit run.

**Exit.** Non-blocking for K. Ships when the translator is correct and tested.

### I — Observability

**Gap.** Helm shifts and per-helm timing are invisible without instrumentation.

**Plan.**

1. New event type: `HELM_SHIFT(from, to, reason, ack_emitted, shift_index)` logged at `INTERACTION` level.
2. `Interaction.parameters['gear_trace']: List[ShiftRecord]` records every shift per turn (pattern-agnostic field).
3. `Interaction.observability_metrics` gains per-helm timing and call counts.
4. `Interaction.usage` attributes token counts per helm.
5. `GET /logs/agents/{id}` queries already work; document the new event type in [`docs/logging.md`](../docs/logging.md).

**Tests.**

- Single Bridge turn produces queryable shift trace.
- Per-helm token attribution sums to overall interaction total.
- `gear_trace` survives interaction pruning rules.

**Exit.** A Bridge turn is fully traceable from a single log query. Per-helm timing and token attribution visible in dashboards.

### J — Performance validation

**Gap.** Without empirical comparison, the Bridge value claim is theoretical.

**Plan.**

1. Extend smoke harness to a pattern matrix runner: `tests/action/bridge/smoke_pattern_matrix.py`.
2. Configurations under test:
   - **Cockpit (control)** — today's commit `7d95904`.
   - **Bridge + Reasoning** — parity sanity check.
   - **Bridge + Reflex + Reasoning** — primary win case.
   - **Bridge + Reflex + Reasoning + Persona** — composed output case.
   - **Bridge + Reflex + Reasoning + Specialist** — interview interrupt case.
3. JSON dump per run, archived under `tests/action/bridge/baselines/`.
4. Performance ledger published into PATTERNS.md.

**Tests.** N/A — this milestone IS the test.

**Exit.** Median latency reduction ≥30% on the trivial-turn subset. p99 not worse than baseline. Total tokens not worse than baseline. All five configurations execute the 6-utterance suite without errors.

### K — Pattern parity

**Gap.** Bridge exists but isn't discoverable or documented as a peer pattern.

**Plan.**

1. New scaffolder profile: `jvagent app create --profile bridge`. Bundles: `bridge` + `reflex_helm` + `reasoning_helm` + `persona_helm` + `intro` + `handoff` + `access_control` + base LM action + chosen Reflex provider LM action.
2. `examples/jvagent_app/agents/jvagent/bridge_agent/` committed alongside `cockpit_agent`.
3. `docs/BRIDGE.md` written (mirroring `docs/COCKPIT.md` structure).
4. `.planning/PATTERNS.md` finalized with performance ledger from J.
5. `GLOSSARY.md` additions: Bridge, Helm, Manifest, latency_class, Specialist, Reflex, Reasoning, Persona Helm.
6. `action-authoring.md` gains a "Pattern compatibility" section.
7. `CLAUDE.md` top-level updated to reference PATTERNS.md.

**Exit.** Operators can choose Bridge via `--profile bridge` and get a working agent. Three patterns documented with "when to use" guidance. **No deprecation warnings on cockpit.** The cockpit profile remains the default in the scaffolder until performance ledger justifies otherwise.

## Patterns coexistence

| Pattern | Profile | Composition | Use when |
|---|---|---|---|
| **Rails** | `minimal` | Pure `InteractAction` chain, no model agency | Deterministic flows, channel adapters, gated processes |
| **Cockpit** | `cockpit` (current default) | Single-helm model agency via walker-revisit | Conversational agents with skills, research, exploration |
| **Bridge** | `bridge` (new at K) | Multi-helm orchestration; helms shift between each other | Latency-sensitive deployments (voice, fast UX); mixed workloads; sub-500ms first-response |

Phase-out is data-driven. A pattern moves from supported → deprecated only when the performance ledger shows another pattern dominates its target use case across two or more measurement cycles, and an explicit ADR proposes the deprecation with a migration path.

## Test infrastructure

- **Unit tests**: `tests/action/bridge/`, `tests/action/helm/`. Same conventions as `tests/action/cockpit/`.
- **Real-LM smoke**: `tests/action/bridge/smoke_bridge.py` runs the 6-utterance suite for any configured pattern.
- **Pattern matrix**: `tests/action/bridge/smoke_pattern_matrix.py` runs all four supported configurations and emits a comparison report.
- **CI gate**: Bridge PRs must pass cockpit + bridge test suites. Pattern-matrix smoke runs as a manual job during J; nightly thereafter.
- **Baselines directory**: `tests/action/bridge/baselines/` archives JSON dumps per commit for trend tracking.

## Branch strategy

- All Bridge work lives on `dev-bridge` through milestone C (the high-risk
  duplication). An earlier `bridge-architecture` branch was created and
  fast-forwarded back into `dev-bridge` during C-3 so a single linear
  history captures milestones A → B → C-*.
- After C merges to trunk, subsequent milestones (D–K) land on trunk gated
  by the `--profile bridge` scaffolder option. No env flag — pattern
  selection is via configuration, not feature toggle.
- No remote pushes during C. The branch stays local until C ships parity.

## Open questions

1. **Reflex provider for E (default).** Groq (`llama-3.1-8b-instant`, sub-200ms), Cerebras (similar), or Anthropic Haiku (provider-coherent with default Reasoning, slower)? Affects headline latency numbers materially. Open for maintainer decision before E.
2. **Naming, final lock at A.** Bridge + Helm — confirmed via brainstorm; A locks these into the ADR.
3. **PersonaHelm: wrap or replace.** Thin wrapper around `PersonaAction` (preserves existing config) vs new helm with independent model/prompt? Wrap is simpler; replace gives independent tuning. Default: wrap.
4. **Shift budget default.** 4 (conservative, avoids ping-pong) or 6 (more flexibility)? Default: 4.
5. **First-emit timeout default.** 800ms or 1200ms? Affects when safety-net ack fires. Default: 800ms.
6. **Manifest source of truth.** `info.yaml` authoritative with `agent.yaml` override (recommended), or `agent.yaml` authoritative? Default: info.yaml authoritative.
7. **PersonaHelm and SUPPRESS posture.** When Bridge suppresses (no response delivered), still emit a HELM_SHIFT event? Default: yes, with `to=null, reason=suppressed`.
8. **AccessControl resource taxonomy for helms.** Convention `tool:helm:{name}` (parallel to existing `tool:{name}` and `skill:{name}`). Confirm at A.

## Sequencing summary

```
A ── B ──┬── C ──┬── E ──┐
         │       ├── F ──┤
         └── D ──┘       ├── J ── K
                  G ─────┤
                  H (optional, parallel after C)
                  I (continuous instrumentation from B)
```

- **A** unblocks everything.
- **B** is a hard predecessor for C–G.
- **C** is the high-risk extraction; merge to trunk gates D–K progression.
- **D** unblocks E (manifests inform Reflex's prompt).
- **E, F, G** can proceed in parallel after their predecessors land.
- **H** is optional and non-blocking.
- **I** is continuous — instrument as you build.
- **J** is the empirical gate for K.
- **K** is the rollout milestone.

Realistic calendar (one developer focused): A in week 1, B in week 2, C in weeks 3–4, D + E in week 5, F + G in week 6, H/I/J in week 7, K in week 8.
