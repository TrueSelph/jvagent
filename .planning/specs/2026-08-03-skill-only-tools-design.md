# Skill-only tools — capability gating via `skill_only_tools` — design

**Date:** 2026-08-03
**Status:** Implemented — see [ADR-0043](../adr/0043-skill-only-tools.md) for the durable decision record and [the plan](../plans/2026-08-03-skill-only-tools.md) for the task breakdown. Shipped on `feat/skill-only-tools` (`64db2dad`…`baa7e3ce`). §9 records where implementation diverged from this document.
**Scope:** `jvagent/action/orchestrator/` (`orchestrator_interact_action.py` `_assemble_tools`, `catalog.py`), `tests/action/orchestrator/`, docs (`docs/ORCHESTRATOR.md`, `.planning/reference/configuration-keys.md`, `docs/scaffolding.md`, `jvagent/scaffold/builtin_profiles/orchestrator.yaml`, `CHANGELOG.md`), new `.planning/adr/0043-skill-only-tools.md`.
**Relation:** Extends [ADR-0018](../adr/0018-lean-tool-surfacing.md) (lean surfacing + `pinned_tools` + `denied_tools`) and [ADR-0012](../adr/0012-skill-executive-architecture.md) (unified tool surface, `find_tool`/`use_skill`).

---

## 1. Context

The Orchestrator assembles one unified tool surface per turn
([`orchestrator_interact_action.py:825`](../../jvagent/action/orchestrator/orchestrator_interact_action.py)).
Three levers exist today for shaping it:

| Lever | Effect |
|---|---|
| `lean_tool_threshold` / `lean_presurface_k` | **Hides** the long tail from the prompt; still `find_tool`-reachable and dispatchable. |
| `pinned_tools`, `always-active: true` | **Forces visible** every turn, surviving lean. |
| `denied_tools` | **Removes** matching names from `tools` — not listed, not discoverable, not dispatchable. |

There is no middle position between "freely callable" and "gone." An operator who
wants a sensitive or procedure-bound capability — a payment charge, a record
deletion, an outbound commitment — to run **only inside its SOP** has to choose
between leaving it directly callable (the model may fire it on a bare user
request, skipping the SOP's preconditions) and denying it outright (the skill
that legitimately needs it also loses it).

Two properties of the current surface make this a real gate problem, not a
visibility problem:

1. **Dispatch resolves against the full surface**, so a hidden tool is still
   callable by name ([`catalog.py:1-18`](../../jvagent/action/orchestrator/catalog.py)).
2. **`block_raw_tool_invocation` auto-promotes** a real tool the model names,
   precisely so lean hiding never dead-ends the loop
   ([`loop.py:1017-1028`](../../jvagent/action/orchestrator/loop.py)).

So hiding a tool cannot restrict it. Only removing it from `tools` (the deny
path) or refusing at call time can.

## 2. Goals / non-goals

**Goals**

- A per-agent, YAML-declared set of tools that are callable **only while a skill
  that declares them is active**, and never on a direct model decision.
- Deployment-time policy: no change to the tool's own signature, owning Action,
  or implementation.
- The gate holds at **dispatch**, so it cannot be defeated by lean promotion,
  `block_raw_tool_invocation`, `load_tool`, or a model naming the tool directly.
- A discovery path that teaches the model the correct move (`use_skill`) instead
  of dead-ending it.
- Fail closed on misconfiguration.

**Non-goals**

- Per-user or role-based authorization — that is `AccessControl` via
  `wrap_action_tool(access_label=...)`
  ([`tools.py:37`](../../jvagent/action/orchestrator/tools.py)), a separate axis
  that composes with this one.
- Changing skill activation, turn-lock, or `requires-tasks` mechanics.
- Touching per-server MCP `denied_tools`
  ([`mcp_action.py:90`](../../jvagent/action/mcp/mcp_action.py)).
- Any new wrapper action or duplicated "skill version" of a tool.

## 3. Locked decisions

| # | Decision | Choice |
|---|---|---|
| 1 | What unlocks a gated tool | **Skill-derived ownership.** A gated tool is callable while an *active* skill lists it in its `allowed-tools` (`SkillDoc.requires_tools`). Config marks *which* tools are gated; ownership is never restated in YAML, so it cannot drift from SKILL.md. |
| 2 | Discoverability | **Hidden from the prompt list, annotated in `find_tool`** — `payments__charge: … (via skill: checkout)`. The model learns the right move rather than concluding the capability is absent. |
| 3 | Violation behavior | **Refuse and steer.** The tool does not run; the observation names the skill(s) to activate. Preserves the SOP's ordering, preconditions and `requires-tasks` detours — the reason the tool is gated at all. |
| 4 | Orphan (gated, no owning skill) | **Fail closed** — permanently locked for the turn, with a `logger.warning` at assembly naming the orphans. A glob typo cannot silently open a hole. |
| 5 | Config surface | Mirrors `denied_tools`: fnmatch globs, empty default, channel-overridable via `channel_overrides.skill_only_tools` (**replaces** the action-level list on that channel). |
| 6 | Precedence | `denied_tools` > `skill_only_tools` > `pinned_tools`. A pin cannot un-gate: a pinned gated tool is **neither listed nor callable** until its skill is active. (`always-active` is not in this chain — it confers *ownership*, see decision #1, and so legitimately unlocks.) |
| 7 | Protected names | `_STEER_EXEMPT` (egress + catalog meta-tools) cannot be gated — dropped from the match with a warning, same as deny. |
| 8 | Implementation shape | **Guard wrapper installed at assembly**, not a check at the loop's dispatch site — the gate travels with the tool object, so every dispatch path is covered. |

## 4. Configuration surface

```yaml
# agent.yaml — orchestrator action
skill_only_tools:
  - "payments__*"
  - "crm__delete_*"

channel_overrides:
  whatsapp:
    skill_only_tools: ["payments__*"]   # REPLACES the action-level list here
```

New attribute on `OrchestratorInteractAction`, declared next to `denied_tools`
([`orchestrator_interact_action.py:669`](../../jvagent/action/orchestrator/orchestrator_interact_action.py)):

```python
skill_only_tools: List[str] = attribute(
    default_factory=list,
    description=(
        "Tool-name globs callable ONLY while a skill that declares them in its "
        "allowed-tools is active (activated this turn, holding the turn-lock, or "
        "always-active). Not listed in the prompt; find_tool shows them annotated "
        "with the owning skill. A direct call is refused with a steer to use_skill. "
        "A gated tool no available skill declares is uncallable (fail closed). "
        "denied_tools wins over this; a pinned_tools match cannot un-gate it. "
        "Egress and catalog meta-tools cannot be gated. Empty by "
        "default. Channel-overridable via channel_overrides.skill_only_tools "
        "(replaces the action-level list on that channel)."
    ),
)
```

`channel_overrides`' own description
([`:607`](../../jvagent/action/orchestrator/orchestrator_interact_action.py)) gains
`skill_only_tools` in its supported-keys list.

`compute_tool_surface_config_hash`
([`catalog.py:48`](../../jvagent/action/orchestrator/catalog.py)) gains
`str(getattr(orch, "skill_only_tools", "") or "")` so the per-agent surface cache
invalidates when the list changes.

## 5. Detailed design

### 5.1 Where the pass runs

The pass splits in two, for reasons §5.6 makes concrete:

- **Glob match + owner index** run just *before* the catalog build
  ([`:1204`](../../jvagent/action/orchestrator/orchestrator_interact_action.py)),
  because `find_tool`/`load_tool` need the annotation map at construction time.
- **Wrapper install + `visible`/`longtail` discard** run at the **end of
  `_assemble_tools`**, after the deny pass
  ([`:1230-1243`](../../jvagent/action/orchestrator/orchestrator_interact_action.py))
  and after the pins ([`:1222-1227`](../../jvagent/action/orchestrator/orchestrator_interact_action.py)).

Installing last is what makes precedence (decision #6) fall out of ordering
rather than needing explicit rules: deny has already popped its matches from
`tools`, so there is nothing left to gate; pins have already written to
`visible`, so the gate's discard is what survives.

At that point in the function the following are already settled and available:

- `tools` — the full surface, post-deny.
- `visible` — the prompt set, post-lean, post-pins.
- `longtail` — the hideable set.
- `docs` — skill docs after `requires-actions` enforcement
  ([`:1142`](../../jvagent/action/orchestrator/orchestrator_interact_action.py))
  and the per-channel gate ([`:1151-1177`](../../jvagent/action/orchestrator/orchestrator_interact_action.py)).
- `activated: List[str]` — created in the loop at
  [`loop.py:173`](../../jvagent/action/orchestrator/loop.py) and passed in
  ([`:828`](../../jvagent/action/orchestrator/orchestrator_interact_action.py)).

### 5.2 The owner index

```python
gated = self._match_tool_globs(
    list(self._channel_cfg(visitor, "skill_only_tools", self.skill_only_tools)),
    set(tools.keys()),
)
protected = gated & _STEER_EXEMPT           # warn + drop, same as deny
gated -= _STEER_EXEMPT

owners: Dict[str, Tuple[str, ...]] = {}     # tool name -> owning skill names
always_on: Set[str] = set()                 # skills active without activation
for d in docs:
    for t in getattr(d, "requires_tools", ()) or ():
        if t in gated:
            owners[t] = owners.get(t, ()) + (d.name,)
    if getattr(d, "always_active", False):
        always_on.add(d.name)
```

`owners` is built from `docs` — the list already filtered by `requires-actions`
and by the current channel. Consequence, intended: **a skill hidden on this
channel does not own its tools on this channel**, so they fall to the orphan rule
rather than leaking. This composes with the existing channel-blocked cleanup that
drops a blocked skill's `requires_tools` from `tools` entirely
([`:1164-1176`](../../jvagent/action/orchestrator/orchestrator_interact_action.py)) —
where that cleanup already removed the tool, gating never sees it.

Orphans are logged once per assembly:

```python
orphans = sorted(gated - set(owners))
if orphans:
    logger.warning(
        "orchestrator: skill_only_tools matched tools no available skill "
        "declares — uncallable this turn: %s", orphans,
    )
```

### 5.3 Active-skill predicate

A skill counts as **active** when any of:

- its name is in `activated` (mutated in place by `use_skill` at
  [`catalog.py:328`](../../jvagent/action/orchestrator/catalog.py), by
  auto-start ([`:1960`](../../jvagent/action/orchestrator/orchestrator_interact_action.py)),
  and by the unlocked-skill surface path ([`:1683`](../../jvagent/action/orchestrator/orchestrator_interact_action.py))),
- it holds the turn-lock (the task-lock doc is appended to `activated` on the
  same paths, so this is covered by the membership test),
- it is `always-active: true`.

The third case means an always-active owner effectively un-gates its tools. That
is coherent: an always-active SOP is in force on every turn, so "only inside that
SOP" is always true.

Because `activated` is a **mutable list captured by reference**, the predicate is
evaluated per tick at call time, not frozen at assembly. A skill activated on
tick 2 unlocks its tools for tick 3 with no re-assembly.

### 5.4 The guard wrapper

```python
def _install_gate(name: str) -> None:
    tool = tools[name]
    inner = tool.run
    owning = owners.get(name, ())

    async def _gated(args: Dict[str, Any]) -> str:
        if owning and any(o in activated or o in always_on for o in owning):
            return await inner(args)
        return _skill_only_steer(name, owning)

    tools[name] = replace(tool, run=_gated)   # dataclasses.replace on SkillTool

for name in gated:
    _install_gate(name)
    visible.discard(name)
    longtail.discard(name)
```

`SkillTool` is a plain dataclass ([`tools.py:22`](../../jvagent/action/orchestrator/tools.py)),
so `dataclasses.replace` preserves `name`, `description` and `terminal` while
swapping the runner. `terminal` is preserved deliberately: gating an IA-as-tool
must not change the loop's end-of-turn semantics once the tool is legitimately
reached.

Discarding from `visible` and `longtail` keeps the gated tool off the prompt. It
is **not** removed from `tools` — that is what keeps it `find_tool`-annotatable
and what lets the normal activation path resurface it: `use_skill` already does
`visible.update(present)` ([`catalog.py:347`](../../jvagent/action/orchestrator/catalog.py))
for the skill's declared tools, so no new surfacing code is needed anywhere.

### 5.5 Steer observations

```python
def _skill_only_steer(name: str, owning: Tuple[str, ...]) -> str:
    if not owning:
        return (
            f"({name} is not directly callable and no available skill provides "
            "it. Tell the user you cannot do that; do not retry.)"
        )
    return (
        f"({name} is only available inside a skill. Call use_skill with one of: "
        f"{', '.join(owning)} — then call {name} again.)"
    )
```

The orphan wording ends the line of attack explicitly. Without it the model
re-tries the same call, hits the loop's repeat-guard, and loses the turn to a
condition it could never satisfy.

### 5.6 Catalog annotation

`build_catalog_tools(all_tools, visible)`
([`catalog.py:136`](../../jvagent/action/orchestrator/catalog.py)) gains an
optional `gated: Optional[Dict[str, Tuple[str, ...]]] = None` parameter (tool
name → owning skill names; empty tuple = orphan). Default `None` keeps every
existing call site and test unchanged.

`_find` appends a marker to a hit's line:

```
[payments]
- payments__charge: Charge a saved payment method. (via skill: checkout)
- payments__refund: Refund a settled charge. (not directly callable; no skill provides it)
```

`_load` returns the full description plus the same marker sentence. Load still
succeeds — it is a description fetch, and the wrapper is the actual gate. An
operator reading logs sees the same explanation the model saw.

The orchestrator passes `gated={n: owners.get(n, ()) for n in gated}` when it
builds the catalog tools ([`:1205`](../../jvagent/action/orchestrator/orchestrator_interact_action.py)).
Since gating is computed at the end of `_assemble_tools` and the catalog is built
earlier, gating computation splits: the **glob match + owner index** move to just
before the catalog build, and the **wrapper install + visibility discard** stay
at the end (after deny). Deny still wins: a denied tool is popped from `tools`,
so `find_tool` — which closes over the same `tools` dict — cannot return it
regardless of what the earlier gated set contained.

### 5.7 Composition with existing behavior

| Interacts with | Result |
|---|---|
| Lean surfacing | Gated tools are dropped from `visible`/`longtail` after the lean pass, so a relevance pre-surface hit does not expose them. |
| `pinned_tools` | The pin re-adds to `visible` at [`:1222`](../../jvagent/action/orchestrator/orchestrator_interact_action.py); the gate discards it afterwards. Net effect: neither listed nor callable (decision #6). Listing a tool the model cannot call was rejected — it invites failed calls, the same reason the fully-listed option was rejected for discoverability. |
| `block_raw_tool_invocation` | The auto-promote at [`loop.py:1017`](../../jvagent/action/orchestrator/loop.py) makes a named tool *visible*; the wrapper still refuses to run it. Visibility and callability stay separate concerns. |
| `load_tool` | Adds to `visible`; the wrapper still refuses. |
| AccessControl (`access_label`) | Independent, composes — `wrap_action_tool`'s AC check is inside `inner`, so the skill gate runs first, then AC. |
| Turn-lock / `lock_companions` | Unchanged. A gated tool the locked skill declares is callable while locked; the existing `prune_task_lock_tools_for_actions` pass may still remove it, which wins (it removes from `tools`). |
| Surface cache | `skill_only_tools` joins the config hash, so an edit invalidates the cached surface. |

## 6. Testing

New slice `tests/action/orchestrator/test_skill_only_tools.py`, reusing the
`make_orchestrator` / `make_visitor` fixtures from
[`test_lean_surfacing.py`](../../tests/action/orchestrator/test_lean_surfacing.py).

1. Gated tool is absent from `visible` but present in `tools` after assembly.
2. Dispatch with no owner active returns the steer string and the inner runner is
   never invoked (assert via a spy runner).
3. After `use_skill` on the owning skill, the tool is visible **and** dispatch
   reaches the inner runner.
4. An `always-active: true` owner makes the tool callable on tick 1 with no
   `use_skill` call.
5. A turn-lock-holding owner makes the tool callable.
6. Orphan: locked, orphan-worded observation, warning logged (`caplog`).
7. `denied_tools` beats `skill_only_tools` — the name is gone from `tools`
   entirely and `find_tool` cannot return it.
8. `pinned_tools` + gated → neither listed nor callable (precedence decision #6).
9. `channel_overrides.skill_only_tools` replaces the action-level list on that
   channel and leaves other channels on the action-level list.
10. `_STEER_EXEMPT` names (`reply`, `find_tool`, `use_skill`) cannot be gated —
    ignored with a warning, still callable.
11. A channel-blocked owning skill: its declared tools are already dropped from
    `tools` by the ADR-0032 cleanup
    ([`:1164-1176`](../../jvagent/action/orchestrator/orchestrator_interact_action.py)),
    so gating never sees them — assert absence from `tools`, not orphan-hood.
12. `compute_tool_surface_config_hash` changes when `skill_only_tools` changes.
13. `find_tool` output carries the `(via skill: …)` annotation; `load_tool`
    returns the description plus the marker.

## 7. Documentation

- **New** `.planning/adr/0043-skill-only-tools.md` — the decision record; extends
  ADR-0018's "Hard deny (capability gate)" section with the middle position.
- `docs/ORCHESTRATOR.md` — capability-gate section gains skill-only alongside
  hard deny.
- `.planning/reference/configuration-keys.md` — new key.
- `docs/scaffolding.md` — profile key list.
- `jvagent/scaffold/builtin_profiles/orchestrator.yaml` — commented example next
  to the existing `denied_tools` comment.
- `CHANGELOG.md` — entry under the current unreleased section.

The ADR should state the relationship to
[`docs/thin-harness.md:45`](../../docs/thin-harness.md) ("operations any user may
call directly belong in `Action.get_tools()`, not buried in skill-only
wrappers"). This design does not contradict that guidance: the capability remains
a plain `Action.get_tools()` tool with an unchanged signature and no wrapper
action. What is new is a **deployment-time reachability policy** over an
unchanged capability — the harness stays thin, and the judgment about when the
tool may fire stays in the SOP.

## 8. Risks

| Risk | Mitigation |
|---|---|
| Operator gates a tool no skill declares and the agent silently loses a capability | Fail-closed is the deliberate choice (decision #4), but it is loud: assembly warning naming the orphans, and `find_tool` reports the orphan state to the model, which relays "cannot do that" rather than failing opaquely. |
| Model burns ticks retrying a gated call | The steer names the exact `use_skill` argument; the orphan variant instructs no retry. Repeat-guard remains the backstop. |
| Gate bypassed by a future dispatch path | The wrapper is installed on the tool object, so any `tool.run(...)` is covered — this is why decision #8 rejects a loop-site check. |
| Skill renamed, `allowed-tools` entry dropped | Tool becomes an orphan → locked, warned. Fails closed, not open. |

## 9. Divergences from what shipped

This document is the design as approved. Five things changed during
implementation and review; the ADR and the code are authoritative where they
disagree with §§4–8 above. File:line references in this document are as-of
design time and have since drifted — follow the ADR for current anchors.

1. **Open gates stay listed and unannotated.** §5.4 discarded every gated name
   from `visible` unconditionally. That made an `always-active` owner's tool
   callable but invisible — contradicting §5.3 here and ADR-0018 §5 — and made
   `find_tool` steer the model to `use_skill` for a tool it could already call.
   The install block now skips the discard when `gate.is_open(...)`, and the
   catalog's annotation map excludes open names.

2. **Gated names are excluded from the lean pre-surface pool.** §5.7 claimed the
   lean interaction was benign. It was not: a gated tool could win a top-`k`
   relevance slot and then be discarded, shrinking the model's usable surface by
   one with nothing promoted in its place. The glob match therefore moved to
   *before* the lean policy block (it needs only `tools.keys()`), and the lean
   candidate pool is now `longtail - gated`. The owner index still builds after
   `docs` exists, so §5.6's two-part split stands — it is now a three-part one.

3. **Owner tuples are deduped, order-preserving.** §5.2's sample appended
   unconditionally, so a skill listing the same tool twice in `allowed-tools`
   produced `"use_skill with one of: checkout, checkout"`. Dedup preserves
   first-seen order — a set would make the model-facing string nondeterministic.

4. **A glob matching no tool is warned.** Not in the approved design. It is the
   one silent failure the feature can have: the operator believes a sensitive
   tool is gated while it stays freely callable. Patterns matching a protected
   (`_STEER_EXEMPT`) name route to the protected warning instead, since those are
   structurally ungateable rather than typos.

5. **"Not listed" is not an invariant.** Three paths re-add a gated name to
   `visible` after assembly: `lock_companions` on the turn-lock surface,
   `load_tool`, and `block_raw_tool_invocation`'s auto-promote. Callability is
   still refused in every case — the guarantee is on the wrapper, not on the
   prompt set. Recorded in ADR-0043 §3.

An adversarial bypass review found no path that executes a gated tool without an
owning skill active. It did surface a **pre-existing** defect outside this
scope: `ensure_skill_tools_materialized` re-wraps tools from raw actions with no
gate, which already defeats `denied_tools` and the `tool_servers` MCP gate and
drops the MCP AccessControl label. Our gate survives it only because that path's
callers activate the same doc whose tools they materialize (declaring implies
owning) — now pinned by a test. Tracked separately; see ADR-0043 §6.
