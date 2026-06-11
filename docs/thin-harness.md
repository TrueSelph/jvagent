# Thin harness principle

**Mandatory design contract** for jvagent as a whole — the Orchestrator, Actions, InteractActions, skills, and any subsystem that exposes tools to the model. Future platform changes must preserve this split unless an ADR explicitly supersedes it.

## What it means

| Layer | Role | Thick or thin |
|-------|------|----------------|
| **Harness** (Orchestrator loop, Actions, turn-lock hooks) | Session/state persistence, validation dispatch, hook triggers, tool registration, raw tool JSON, access control | **Thin** — no conversation steering |
| **Skill SOP + model** | Intent routing, turn loop, extraction, when to call which tool, multi-step chaining | **Thick** — model reads and follows |
| **Skill extension / action package** | Domain validators, API side effects, branching, bundled scripts | **Thick** — business logic lives here |

The harness answers: *Is state valid? Did hooks run? What is the tool JSON?*

The model answers: *What did the user mean? Which tool do I call next?*

**Actions expose capabilities; skills and SOPs add judgment.** Do not hide multi-step workflows inside action handlers when the model should coordinate explicit tool calls.

## Platform invariants (foundation — never weaken)

These are **regression boundaries**. Breaking them reintroduces a “fat harness” that fights the model.

1. **Routing is tool selection** — there is no separate semantic router or capability registry. The model picks tools from the assembled surface ([ADR-0012](../.planning/adr/0012-skill-executive-architecture.md), [SPEC §3.3](../.planning/SPEC.md)). Exception: **mechanical turn-lock** ([ADR-0013](../.planning/adr/0013-togglable-deterministic-turn-lock.md)) restricts the callable surface to an active flow's IA tool — that is session bookkeeping, not intent classification.

2. **No server-side intent classification** — cancel, reset, correction, multi-answer routing, and off-topic handling belong in the composed SOP. The foundation must not use regex, keyword lists, or prep observations to choose tools for the model.

3. **No turn-prep steering** — locked-skill prep (`prepare_locked_skill_turn`, `skill_runtime_ready`, etc.) may load session and contract only. It must **not** inject observations that tell the model which tool to call next, auto-seed field values, or attach `pending_directive` hints that replace explicit tool calls.

4. **No activation auto-store** — skill or action activation must not parse the user message and pre-fill session state. Extraction is model-owned via explicit tool calls.

5. **No response inlining** — do not merge downstream tool payloads into upstream tool responses inside the server (e.g. auto-inlining “next step” content into a store response). `next_tool` hints and `response_directive` are allowed; the model still issues separate tool calls per SOP.

6. **No orchestrator action special-casing** — the orchestrator must not post-process one action's tool results to force follow-up tool calls for that action. Turn-lock uses generic bound-action hooks only (`skill_runtime_ready`, `prepare_locked_skill_turn`, `prune_turn_tools`).

7. **Lean tool surfacing** — list tools and skills; let the model discover details via `find_tool` / `load_tool` and `find_skill` / `use_skill` ([ADR-0018](../.planning/adr/0018-lean-tool-surfacing.md)). Do not duplicate full SOPs in tool descriptions.

8. **Foundation stays domain-agnostic** — no per-app field names, business phrases, or skill-specific validators in generic orchestrator or shared action pipeline code. Domain fixes belong in skill `custom_tools.py`, action-local handlers, or skill frontmatter.

9. **Model owns extraction; validators are the gate** — the server does not re-extract values from the user's message or compare model-supplied values against the utterance. Freshness rules ("use only the latest message") live in the SOP; acceptance rules live in declared validators. A harness that second-guesses model extraction is a fat harness.

## Invariant rules (skill and action authors)

1. **Capabilities on Actions** — operations any user may call directly belong in `Action.get_tools()`, not buried in skill-only wrappers ([`jvagent/skills/README.md`](../jvagent/skills/README.md)).

2. **Judgment in skills** — JV skills coordinate existing tools via SOP; Claude skills run bundled scripts in the sandbox. Put routing and acceptance criteria in the skill body or frontmatter, not in orchestrator code.

3. **Locked-in flows expose one IA tool** — multi-turn flows record a control-task, expose `get_tools()` → `execute(visitor)`, and clear the task from their own session logic. They gain no orchestrator-specific resume API.

4. **Model chains explicit tools** — read tool `ok`, `response_directive`, and envelopes; call the next primitive per SOP — not because the server auto-called it.

5. **Hooks are automatic** — validators, pre/post processors, and completion handlers run on triggers. Only entries declared as LLM-callable tools (e.g. `skill_tools`) appear on the callable surface.

## Anti-patterns (reject in review)

| Anti-pattern | Why it violates thin harness |
|--------------|------------------------------|
| Regex cancel/reset detector in orchestrator or action runtime | Intent belongs in SOP |
| `message_evaluation` or prep observations that pre-select tools | Server chooses tools instead of model + SOP |
| Auto-inlining multi-step results into one tool response | Server drives the turn; model skips explicit calls |
| Domain `if signup` branches in shared action code | Foundation absorbs skill logic |
| Wrapping an Action operation only in a skill when it should be a first-class tool | Hides capability; breaks lean surfacing |
| Fat tool schemas that duplicate the full skill SOP | Prompt bloat; model should `use_skill` instead |
| Second extraction path (frontmatter extractors, `extract_*` helpers) for standard utterances | Duplicates model extraction |

## Subsystem profiles

Concrete rules for specific subsystems extend this document — they must not contradict platform invariants.

| Subsystem | Profile |
|-----------|---------|
| **InterviewAction** | [`jvagent/action/interview/docs/thin-harness.md`](../jvagent/action/interview/docs/thin-harness.md) — `interview__*` tools, frontmatter schema, validators as the only store gate |

When adding a new locked-in or skill-backed subsystem, add a profile doc that links here and lists subsystem-specific invariants and tests.

## Relationship to architecture

- **[ADR-0012](../.planning/adr/0012-skill-executive-architecture.md)** — orchestrator as executive; tools are primitives, not hidden workflows.
- **[ADR-0013](../.planning/adr/0013-togglable-deterministic-turn-lock.md)** — turn-lock is a surface restriction, not semantic routing.
- **[ADR-0018](../.planning/adr/0018-lean-tool-surfacing.md)** — progressive disclosure for tools and skills.
- **[ORCHESTRATOR.md](ORCHESTRATOR.md)** — turn loop, flow continuation, tool surface assembly.
- **[SPEC §3.3](../.planning/SPEC.md)** — orchestrator invariants.

## Verification mindset

When adding platform or action features, extend **skill hooks or SOP** first. Touch the foundation only for generic plumbing (session keys, validator invocation, envelope shape) — and add a test that proves steering was not reintroduced.

Subsystem profiles should list concrete test files that guard their contract.

## See also

- [`.planning/reference/action-authoring.md`](../.planning/reference/action-authoring.md) — building new actions on this contract
- [`jvagent/action/CLAUDE.md`](../jvagent/action/CLAUDE.md) — action subsystem guide
- [`jvagent/skills/README.md`](../jvagent/skills/README.md) — skill placement and specs
