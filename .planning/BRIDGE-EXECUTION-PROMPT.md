# Bridge Execution Prompt

> Paste the section below into a Claude Code session opened in the jvagent repo root. The prompt assumes the working directory is the jvagent project and that `.planning/BRIDGE-ROADMAP.md` exists.

---

You are executing the Bridge + Helm architecture buildout for jvagent. The plan already exists at `.planning/BRIDGE-ROADMAP.md` and is the product of a deliberate design conversation with the maintainer. Treat it as authoritative. Do not redesign it. If you find a genuine issue, stop and surface it with citations rather than working around it.

## Required reading before any code touches disk

Read these in order. Cite them by `file:line` in commit messages when your decisions trace to them.

1. `CLAUDE.md` (repo root) — agent guide, conventions, traps.
2. `.planning/BRIDGE-ROADMAP.md` — the plan you are executing. This is the source of truth for scope, milestones, exit criteria.
3. `.planning/SPEC.md` — normative contracts; Bridge must not break invariants 1–8.
4. `.planning/COCKPIT-ROADMAP.md` — historical context for the pattern Bridge sits alongside.
5. `.planning/adr/0002-walker-revisit-cockpit.md` — the walker-revisit pattern Bridge preserves.
6. `jvagent/action/cockpit/CLAUDE.md` and `docs/COCKPIT.md` — the subsystem you'll extract from in milestone C.
7. `jvagent/action/interact/CLAUDE.md` — interact pipeline contract.

When you enter a subsystem directory to edit code, read its local `CLAUDE.md` first.

## Hard constraints (from the plan)

- **No harness subsumption.** Do not modify `InteractWalker`, `response_bus`, `Conversation`/`Interaction`/`User`, or `AccessControlAction` for Bridge. Bridge is a composition of new actions on top.
- **No forced cockpit deprecation.** Cockpit stays first-class through K. No deprecation warnings, no behavioral changes to existing cockpit YAML.
- **Walker-revisit preserved.** Each helm's `step()` issues at most one model call. Use `visitor.prepend([self])` for revisits, matching ADR-0002.
- **Pattern-agnostic abstractions.** Manifest schema, `HELM_SHIFT` event, `Interaction.parameters['gear_trace']` — these live at harness level (loader, observability), not under `bridge/` or `cockpit/`.
- **Additive only.** Existing cockpit YAML continues to work unchanged after every milestone.
- **One model call per walker visit.** This invariant is load-bearing across cockpit and bridge.

## Order of operations

Execute milestones strictly in the order specified in the BRIDGE-ROADMAP.md status snapshot. Each milestone has its own exit criteria — do not advance past a milestone until its criteria are met.

### Milestone A — ADR + contracts (design only, no source code)

Produce two artifacts:

1. `.planning/adr/0007-bridge-helm-architecture.md` defining:
   - `HelmStepResult` verb set (`EMIT`, `EXECUTE`, `SHIFT`, `DELEGATE`, `YIELD`) with exact dataclass shapes and field semantics.
   - `BridgeState` dataclass shape (matches §"Milestone A — plan" in the roadmap).
   - Manifest v0 schema fields and validation rules.
   - AccessControl resource taxonomy (`tool:helm:{name}`).
   - Relationship to ADR-0002 (supersedes in spirit, walker-revisit stays).
   - SPEC.md §3 addendum text — proposed insertions, not applied yet.
2. `.planning/PATTERNS.md` — catalog of Rails / Cockpit / Bridge patterns with decision tree and performance-ledger scaffold.

**HARD STOP at end of A.** Post the ADR and PATTERNS.md as a milestone report. Do not start B until the maintainer explicitly confirms the verb set, state shape, and manifest schema are locked. These contracts are load-bearing for B–G; getting them wrong is the most expensive failure.

### Milestone B — Skeleton + stub helms

Create the directory layout (`jvagent/action/bridge/`, `jvagent/action/helm/`), `BaseHelm`, `BridgeInteractAction` at weight `-200`, `StubHelm`, `BridgeState` plumbing on `visitor._bridge_state`, shift budget, first-emit timeout, AccessControl filter point. No real LM calls. 100% unit-test coverage on new code.

**Branch: `bridge-architecture`** (long-lived through C; merge to trunk only after C ships parity).

### Milestone C — ReasoningHelm parity (highest-risk)

Lift `CockpitEngine` into `ReasoningHelm`. Refactor `CockpitInteractAction` into a thin compat shim that internally constructs Bridge + ReasoningHelm. External cockpit behavior must be unchanged.

Run the cockpit smoke harness (`tests/action/cockpit/smoke_real_lm.py`) after every commit. Archive JSON dumps under `tests/action/bridge/baselines/`.

**HARD STOP if any metric drifts beyond 5% vs commit `7d95904` baseline.** Surface the regression with the diff; do not paper over it. C is the merge gate to trunk — get it right.

### Milestones D–G

D (manifest loader), E (ReflexHelm), F (Specialist delegation), G (PersonaHelm). After C lands on trunk, these may proceed; D unblocks E. F and G can parallel E once D ships.

Each milestone has its own gate criteria in the roadmap. Honor them.

### Milestones H–K

H (migration CLI) is optional and non-blocking — ship when correct. I (observability) is continuous instrumentation from B onward, not a discrete sprint. J (performance validation) is the empirical gate for K. K is the rollout.

## Open questions you must surface BEFORE writing code in their dependent milestones

The roadmap §"Open questions" lists eight items. Two need explicit maintainer answers — do not pick defaults silently:

1. **Reflex provider for milestone E default.** Groq / Cerebras / Anthropic Haiku. Affects headline latency at J.
2. **Branch strategy confirmation.** Default in plan: feature branch through C, then trunk with profile gating. Confirm.

The remaining six items have plan-stated defaults. If you find reason to deviate, surface; otherwise proceed with the defaults.

Surface all open questions at the end of milestone A's report.

## Working style

- **Type-annotate everything.** Pydantic and jvspatial both depend on it.
- **Use `attribute(...)` for all persisted Node fields.** Plain class attributes don't persist.
- **Add tests for any new behavior** in `tests/action/bridge/` or `tests/action/helm/`, mirroring the cockpit layout.
- **Run `pre-commit run --all-files`** before claiming a milestone done.
- **Cite `file:line` in commit messages and PR descriptions.** "Fixed routing in `cockpit/routing/router.py:142`" beats "fixed routing."
- **Honor lifecycle hooks** on every new Action subclass: `on_register`, `on_enable`, `on_startup`, `on_disable`, `on_deregister`.
- **No top-level CLAUDE.md, SPEC.md, or PATTERNS.md edits outside the changes the roadmap explicitly authorizes for that milestone.** Per the roadmap, the harness stays pattern-agnostic and the SPEC additions are reviewed before applied.

## Milestone reports

At the end of every milestone, post a brief report:

- What landed (file paths, key classes/functions added).
- Tests added and results.
- Metrics deltas vs baseline (if applicable — required for C and J).
- Open questions surfaced or resolved.
- Next milestone you intend to start.

Wait for explicit go-ahead between milestones A→B, B→C, and C→D. Other transitions (D→E, E→F, F→G, etc.) may proceed if their exit criteria are clearly met and no open questions remain — but err on the side of asking.

## What to do if you disagree with the plan

The plan was designed in deliberate conversation. If you find a genuine issue — a contradiction with SPEC.md, a missing dependency, a test infrastructure gap, a constraint that cannot be satisfied — **stop and surface it** with file:line citations to the conflicting material. Do not redesign around it. The maintainer prefers reviewing problems to discovering them in code review.

## First action

Read the required materials above. Then propose your draft of milestone A (the ADR + PATTERNS.md) as text inline for review. Do not write source code, do not edit the roadmap, do not start B. Wait for review before any further moves.

---

> End of prompt. The maintainer's preferences: experienced developer, prefers reviewing plans before building, values pluggability and additive design.
