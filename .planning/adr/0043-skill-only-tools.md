# ADR 0043 — Skill-only tools (`skill_only_tools`)

**Status**: Accepted
**Date**: 2026-08-03
**Relation**: Extends [ADR-0018](0018-lean-tool-surfacing.md) §"Hard deny (capability gate)" with the missing middle between freely-callable and denied. Builds on [ADR-0012](0012-skill-executive-architecture.md) (unified surface, `find_tool`/`use_skill`).

---

## 1. Context

Three levers shape the assembled surface today: lean surfacing **hides**,
`pinned_tools` / `always-active` **force visible**, `denied_tools` **removes**.
There is nothing in between. An operator who wants a sensitive or
procedure-bound capability to run only inside its SOP must choose between
leaving it directly callable (the model fires it on a bare request, skipping the
SOP's preconditions) and denying it (the skill that needs it also loses it).

Hiding cannot substitute for a gate. Dispatch resolves against the full surface,
and `block_raw_tool_invocation` deliberately auto-promotes a real tool the model
names (`loop.py:1017`) so lean never dead-ends the loop.

## 2. Decision

Add `skill_only_tools`: fnmatch globs of tools callable **only while a skill
that declares them in its `allowed-tools` is active**.

- **Ownership is skill-derived.** Config marks *which* tools are gated; the
  owner index is built from `SkillDoc.requires_tools`, so YAML and SKILL.md
  cannot drift.
- **Active** = activated this turn (`use_skill`, auto-start, or holding the
  turn-lock — all append to `activated`) or `always-active: true`.
- **Not listed, but discoverable.** Gated tools are dropped from the prompt set;
  `find_tool` returns them annotated `(via skill: checkout)` so the model learns
  the right move instead of concluding the capability is absent.
- **Refuse and steer.** A direct call does not run; the observation names the
  skill to activate. This preserves the SOP's ordering, preconditions and
  `requires-tasks` detours — the reason the tool is gated at all.
- **Fail closed.** A gated tool no available skill declares is uncallable, with
  a warning at assembly. A glob that matches no tool is also warned — that is
  the one silent failure the feature can have, since the operator would believe
  a tool is gated while it stays freely callable.
- **Precedence:** `denied_tools` > `skill_only_tools` > `pinned_tools`. Egress
  and catalog meta-tools cannot be gated. Channel-overridable via
  `channel_overrides.skill_only_tools` (replaces the action-level list).

Implemented as a guard wrapper installed on the tool object
(`skill_gate.install_skill_gate`), not a check at the loop's dispatch site, so
every dispatch path is covered. Precedence falls out of statement order in
`_assemble_tools`: the gate installs last, after deny has popped its matches
from `tools` and after pins have written to `visible`.

## 3. Consequences

- A third, weaker capability gate exists between lean-hidden and denied.
- `use_skill` becomes load-bearing for gated capabilities rather than advisory.
- The gate composes with AccessControl (`access_label`) rather than replacing
  it: skill gate first, then per-user authorization.
- A misconfigured glob costs a capability, not a safety hole.
- **The gate protects callability, not visibility, on the turn-lock surface.**
  `restrict_tools_to_task_lock_skill` recomputes the visible set from the locked
  skill's allowed names, so a gated tool matched by a `lock_companions` glob is
  re-listed in the prompt even though assembly had discarded it. The wrapper
  still refuses the call, so nothing leaks — but do not read "not listed" as an
  invariant that holds on every surface.

## 4. Relationship to the thin-harness principle

[`docs/thin-harness.md`](../../docs/thin-harness.md) says operations any user may
call directly belong in `Action.get_tools()`, "not buried in skill-only
wrappers." This does not contradict that: the capability remains a plain
`get_tools()` tool with an unchanged signature, and no wrapper action or
duplicate tool is introduced. What is new is a deployment-time *reachability
policy* over an unchanged capability. The harness stays thin; the judgment about
when the tool may fire stays in the SOP.

## 5. Alternatives considered

- **Check at the loop dispatch site** — readable, but guards only that one call
  site; other paths that call `tool.run` directly would bypass it.
- **Remove from `tools` and splice back on activation** — mirrors `denied_tools`
  exactly, but activation happens in several places mid-loop and each would need
  the splice; `find_tool` would need a side dict anyway for the annotation.
- **Explicit `{tool_glob: [skills]}` map in YAML** — most explicit, but restates
  ownership already declared in SKILL.md and drifts on rename.
- **Auto-activate the owning skill and run the tool** — fastest, but the SOP
  body arrives *after* the side effect fired and `requires-tasks` gating never
  gets to push its detour.

## 6. Known limitation

The gate lives on the tool object, so it covers every dispatch path — but
"every object in `tools` is gated" is not enforced by construction.
`ensure_skill_tools_materialized` (`skill_tasks.py`) re-wraps tools from raw
actions with no gate. It is safe today only because its callers activate the
same doc whose `requires_tools` they materialize (declaring implies owning),
which `tests/action/orchestrator/test_skill_only_tools.py` now pins as a
contract. A future caller that materializes without activating would open a
hole. (That same path already defeats `denied_tools` and the `tool_servers` MCP
gate — a separate pre-existing defect, tracked independently of this ADR.)

Covered by `tests/action/orchestrator/test_skill_only_tools.py`.
