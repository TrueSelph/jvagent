# ADR-0037 — Parameters and directives are the only customization surfaces

- **Status:** Accepted (implemented)
- **Date:** 2026-07-25
- **Relates to:** [ADR-0012](0012-skill-executive-architecture.md) (orchestrator), [ADR-0015](0015-skill-executive-configuration-surface.md) (config surface), [ADR-0024](0024-single-per-turn-egress.md) (single per-turn egress), [ADR-0025](0025-replyaction-single-output-contract.md) (ReplyAction output contract)
- **Supersedes in part:** ADR-0015's premise that behaviour is tuned through per-action prompt attributes

---

## 1. Context

An operator changing how an agent behaves should have exactly two places to look:
**parameters** (standing rules) and **directives** (per-turn instructions). That is
the stated architecture. It is not what the code does today.

A single behavioural rule — *"don't state facts you haven't verified"* — is
currently expressed in three places:

1. a `response`-scoped parameter in `core_parameters()`,
2. the OPERATING RULES text rendered from that parameter into the loop prompt,
3. a hardcoded loop guard (`enforce_grounded_claims` /
   `enforce_grounded_specifics`) added because the model ignored 1 and 2.

Three sources of truth for one rule. An operator who edits the parameter does not
change the guard; one who disables the guard does not change the prompt.

### Behaviour-shaping surfaces as they exist

| Kind | Instances |
|---|---|
| **Parameters** | the `parameters` list (`orchestration` / `response` scoped) |
| **Prompt text** | `system_prompt`, `user_prompt`, `planning_prompt`, `memory_prompt`, `tool_use_policy_prompt`, `flow_in_progress_prompt`, `length_limit_prompt`, `finalize_prompt`, `safeguards_reminder`, `no_skills_text`, `clarify_text`, `ack_statements` |
| **Code guards** | `block_raw_tool_invocation`, `lock_active_flow`, `enforce_grounded_claims`, `enforce_grounded_specifics`, `plan_completion_max_deflections` |
| **Loop deflections** | five `(guard)` observation sites (chain, plan-drain, repeat, two grounding) |
| **Deterministic scrub** | `vet_egress` (self-identification, cutoff claims, closers, duplicate greeting) |
| **Skills / directives** | SKILL.md SOPs, interview directives, review re-entry guard |

### Why the guards exist

They are not gratuitous. Prompt-only enforcement was measured three times on
gpt-4.1 during this work and failed each time:

- **Injection resistance** plateaued at ~88% (22/25) on prompt alone.
- **Duplicate greeting** got *worse* under two rounds of prompt tightening — the
  second attempt made the model drop the introduction in 3 of 4 runs.
- **Fabricated specifics**: the grounding parameter said precisely the right
  thing and a light-gear turn still answered *"taught at the University of
  Toronto"* having retrieved nothing.

So the constraint is real: **a parameter rendered as prose is probabilistic.**
Any architecture that makes parameters the only surface must not also make prose
the only enforcement, or it re-introduces all three failures.

---

## 2. Decision

**Parameters and directives are the only surfaces through which agent behaviour
is customized.** Everything else is either mechanics or an *enforcement strategy
owned by a parameter*.

Three rules follow.

### 2.1 The behaviour/mechanics test

A prompt block is **behaviour** — and must become a parameter — if it tells the
model *who to be* or *what it may say*. It is **mechanics** — and stays as prompt
text — if it explains *how the loop works*.

| Block | Verdict |
|---|---|
| OPERATING RULES | already parameters ✓ |
| `memory_prompt` | behaviour → parameter |
| `tool_use_policy_prompt` | behaviour → parameter |
| `length_limit_prompt` | behaviour → parameter (a conditional rule) |
| `clarify_text` | **revised — stays.** See below. |
| `ack_statements` | **revised — stays.** See below. |
| `safeguards_reminder` | enforcement of existing parameters → becomes a *placement*, §2.2 |
| LOOP PROTOCOL, `user_prompt`, `finalize_prompt`, `no_skills_text` | mechanics — stay |
| `planning_prompt` | mechanics (how `update_plan` works) — stays |
| `flow_in_progress_prompt` | mechanics (continuation state) — stays |

**Correction, made during implementation.** `clarify_text` and `ack_statements`
were listed above as behaviour. They are not. A parameter *instructs the model
about* what to say; these two *are* what is said — literal strings emitted
without a model call. Converting them would have turned a canned fallback into a
model round-trip on the exact paths chosen to avoid one. They stay as text, and
the §2.3 concern about ungoverned output is met a different way: both leave
through `publish()`, which applies `vet_egress` for user-facing categories, so
they are governed at egress by the same parameters as any other reply.

The three genuine conversions (`tool_use_policy_prompt`, `length_limit_prompt`,
`memory_prompt`) landed as `placement: inline` rules rather than as bullets in
the OPERATING RULES block. Prompt *position* here was tuned by live measurement
— moving the OPERATING RULES block mid-prompt during this work dropped injection
resistance from 5/5 to 2/5 — so a conversion that also relocated text would have
been an unmeasured behavioural change wearing a refactor's clothes. `inline`
keeps each rule at its existing position while making it a real parameter:
one source of truth, overridable and deletable by key.

### 2.2 A parameter declares how it is enforced

Parameters gain two optional fields. Defaults preserve today's behaviour exactly.

```yaml
- scope: response
  condition: "the user asks for a fact you have not retrieved"   # optional, today
  response: "Don't state specifics you haven't verified."        # required, today
  enforcement: guard        # prompt (default) | scrub | guard
  placement: user_turn      # system (default) | user_turn
```

- **`enforcement: prompt`** — rendered into the scope's prompt. Today's behaviour,
  and the default, so every existing parameter is unaffected.
- **`enforcement: scrub`** — additionally enforced deterministically at egress.
  This is what `vet_egress` already does for the core response rules; it stops
  being a hardcoded list and becomes the set of parameters marked `scrub`.
- **`enforcement: guard`** — additionally enforced as a bounded loop deflection.
  This is what the grounding guards do; `enforce_grounded_claims`,
  `enforce_grounded_specifics` and `plan_completion_max_deflections` stop being
  orchestrator config and become properties of the parameters that state those
  rules.

`placement: user_turn` replaces `safeguards_reminder`: the peak-attention
reinforcement becomes "render these parameters in the user turn as well", not a
separate hand-maintained string that restates them.

A `scrub` or `guard` parameter needs a detector. Detectors are registered by key,
so the parameter names the strategy rather than embedding logic:

```yaml
  enforcement: guard
  detector: unsupported_specifics
```

Unknown detector → the parameter degrades to `prompt` and logs once. That keeps a
config-only deployment safe and makes the failure visible.

### 2.3 Directives stay the per-turn surface

Directives (ADR-0025) already carry per-turn instruction, including model-only
guidance after `U+2063`. No change to the contract. The clarification is that a
directive is the *only* way to shape one turn's output from outside the model,
just as a parameter is the only way to shape standing behaviour. Anything that
today reaches the user without passing through one of the two — a bare
`publish()`, a canned fallback string — is a defect, not a design.


### 2.4 Conflict resolution

Prose conflict is undecidable — nothing can tell that *"be concise"* and *"give
complete detail"* collide. Precedence is therefore **declared, not inferred**.

**C1 — Conflict is only decidable between rules sharing a `key`, within one
`scope`.** An unkeyed rule is additive and never conflicts, which is every
parameter that exists today, so the constraint is opt-in and backward compatible.

**C2 — Tier order derives from source, never hand-set:**

| Rank | Source | Rationale |
|---|---|---|
| 0 | `action` | a capability's own default |
| 1 | `skill` | narrower and transient, so it outranks a capability default |
| 2 | `core` (default) | the framework's opinion, e.g. voice |
| 3 | `agent` | the operator — outranks the framework's *opinion* |
| — | `core` + `inviolable` | the framework's *floor*; outside the ladder |

Numeric priorities were rejected: they invite the arms race where every author
writes `999`. Order derived from source cannot be gamed by the rule's own text.

`tier: agent` is declarable because `agent.yaml` sets the same attribute a plugin
sets programmatically — the code cannot distinguish operator intent without being
told. `tier: core` is ignored, or any config could claim the floor and then
override it.

**C3 — An `inviolable` core rule wins its group outright**, regardless of tier.
The challenger is dropped and the attempt logged once, with its source. A
customization surface that lets a skill quietly disable injection resistance is
worse than no surface. Marked inviolable today: `identity.self_disclosure`,
`identity.cutoff`, `identity.internals`, `safety.injection` — identity and
safety, the things an agent must not be talked out of.

`grounding.verified_claims` and `voice.closers` are deliberately *not*
inviolable. They are the framework's strong opinions, and an operator may
replace them. Grounding in particular carries a `guard` detector, and a
deterministic detector can be wrong deterministically (§3, Risk) — a rule whose
false positives cannot be corrected through the customization surface is not
customizable, which would defeat the ADR.

**C4 — Conflict is per scope.** An `orchestration` and a `response` rule sharing
a key are injected into different prompts; both are legitimate.

**C5 — A conditional rule never suppresses an unconditional one.** It refines.
Overriding needs the same key *and* explicit intent — otherwise `when X: be
brief` silently becomes a global.

**C6 — Prose conditions are prompt-only; deterministic enforcement carries its
condition in the detector.** The loop cannot evaluate *"when the user asks about
pricing"*. So the detector owns applicability (`unsupported_specifics` already
encodes "only when no tool ran") and the prose `condition` renders into the
prompt as it does now. A parameter may carry both; they operate at different
layers and need not agree. **This settles the conditional-enforcement question.**

**C7 — Enforcement ratchets up within an inviolable group.** For a group with an
`inviolable` core rule, a lower tier may raise `prompt → scrub → guard` but never
lower it — otherwise writing a weaker duplicate is a way to switch a safety floor
off. For every other group the winning rule's own `enforcement` stands, including
downward, which is what lets an operator relax a misfiring detector. C7 protects
floors; it does not freeze opinions.

**Rejected: automatic semantic conflict detection** (an LLM pass, or embedding
similarity over rule text). Non-deterministic, unexplainable when it fires, and a
plausible-but-wrong match would silently drop a rule an operator wrote. Keys are
duller and correct.

---

## 3. Consequences

**Good.** One place to look. An operator reads the parameter list and sees every
behavioural rule *and* how strictly each is enforced. Deleting a parameter
removes the prompt text, the scrub and the guard together — impossible today.
Enforcement strength becomes a per-rule decision rather than a global property of
the harness.

**Cost.** The parameter schema, the render sites and the loop all change. Existing
parameters keep working untouched (`enforcement` defaults to `prompt`), but the
orchestrator loses six config attributes, which is a breaking change for anyone
setting them — they move onto the parameters that own those rules.

**Not solved.** Streaming egress still cannot be scrubbed — by the time a rule
could fire, the tokens have left. This is a property of the transport, not of
the design, and is the one place where `prompt` enforcement is the only
enforcement available. `SuggestionsInteractAction` **is** now fixed: the
response rules render into its prompt and each chip is scrubbed before publish
(chips ride in `metadata`, so `publish()`'s scrub of `content` never saw them).

**A detector must be no coarser than the rule that owns it.** `identity.cutoff`
and `identity.self_disclosure` initially shared one detector, so deleting either
rule left the other still enforcing both — which would have made this ADR's
central promise false in exactly the case an operator would test. They now have
one detector each. Any future rule pair that shares a detector inherits the same
defect.

**Risk.** `scrub` and `guard` are deterministic and can therefore be *wrong*
deterministically. A detector with a false positive blocks legitimate replies on
every turn, where a bad prompt rule merely biases. Each detector needs a
two-direction test — what it must catch, and what it must leave alone — of the
kind `unsupported_specifics` has (it flags "University of Toronto" and leaves
"worked as an academic and practitioner" alone).

---

## 4. Migration

1. Add `enforcement` / `placement` / `detector` to the parameter schema; default
   everything to today's behaviour. No functional change.
2. Register the existing detectors (`vet_egress` classes, `unsupported_specifics`,
   `unsupported_source_claim`) under stable keys.
3. Move the core response rules onto `enforcement: scrub`, deleting the hardcoded
   list in `vet_egress`.
4. Move the grounding rules onto `enforcement: guard`, deleting
   `enforce_grounded_claims` / `enforce_grounded_specifics`.
5. Convert `memory_prompt`, `tool_use_policy_prompt`, `length_limit_prompt` to
   `placement: inline` parameters; delete the attributes. (`clarify_text` and
   `ack_statements` are emitted text, not behaviour — see §2.1.)
6. Replace `safeguards_reminder`'s behavioural half with `placement: user_turn`,
   leaving a mechanics-only template.
7. Close the ungoverned paths: suggestions **(done)**; streaming is documented
   as a known limit of the transport (§3).

Steps 1–2 are additive and safe to land alone. Steps 3–6 are the breaking ones
and should land together with a CHANGELOG note.

---

## 5. Open questions

- ~~**Do skills get parameters?**~~ **Settled — yes, implemented.** A SKILL.md
  declares `parameters:` in frontmatter, in the same `{scope?, condition?,
  response}` shape an Action declares programmatically. Both routes pool onto the
  same interaction, so there is one read path. Contributed only while the skill is
  in force (always-active, the active task-lock, or activated this turn) — a
  merely available skill shapes nothing. Activating mid-turn re-renders the loop's
  parameter section so an orchestration-scoped rule applies for the rest of that
  turn.
- ~~**Conditional enforcement.**~~ **Settled — §2.4 C6.**
- ~~**Precedence.**~~ **Settled — §2.4 C1-C4, implemented.** C5 and C7 depend on
  the `enforcement` field from §2.2 and remain unbuilt.
