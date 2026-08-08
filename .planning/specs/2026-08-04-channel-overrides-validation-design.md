# Validate-time checks for `channel_overrides` keys — design

**Date:** 2026-08-04
**Status:** Partially implemented — **Check B shipped**; Check A was reviewed and
dropped as low-yield (see §1: it would not have caught the incident). Also
dropped with it: the known-channel set (§5.1) and the `provides_channels` action
declaration, neither of which Check B needs — reachability is determined from the
agent's own `actions:` refs, since `validate_agent_yaml` operates on the YAML
dict and never loads Action classes. §9 records the as-built deltas.
**Scope:** `jvagent/core/channel.py` (known-channel registry), `jvagent/core/agent_yaml_validator.py` (the checks), `jvagent/cli/validate.py` (severity plumbing), `jvagent/action/base.py` (optional `provides_channels` declaration), channel-adapter actions (declare their channels), `tests/core/`, `tests/cli/`, docs.
**Relation:** Follows the `whatsapp` / `whatsapp_call` misconfiguration found while investigating [ADR-0043](../adr/0043-skill-only-tools.md) channel overrides. Applies to every `channel_overrides` key, not just `skill_only_tools`.

---

## 1. Context — and a correction to the premise

`channel_overrides` is resolved by `_channel_cfg`
([`orchestrator_interact_action.py:2671`](../../jvagent/action/orchestrator/orchestrator_interact_action.py)),
which looks up `visitor.channel` **verbatim**. A block keyed on a channel that
never occurs silently no-ops and the action-level value applies.

The incident that prompted this: an operator put `skill_only_tools` under
`whatsapp`, but voice turns arrive on `whatsapp_call`. The override never
applied.

**The check as literally requested — "validate that `channel_overrides` keys are
known channels" — would not have caught it.** `whatsapp` is a perfectly valid
channel; the key was valid and the *intent* was wrong. Key-validity checking
catches `whatsap` and `whats_app`. It does not catch a correct key on the wrong
channel, which is the failure that actually happened and is the more likely one,
because both keys are real and the example app ships blocks for both.

So this spec proposes two checks. **Check A** is the requested key lint. **Check
B** is the sibling-coverage lint that catches the reported incident. B is the
one that pays for itself; A is cheap and catches a different, real class of typo.

### 1.1 The hard constraint: channels are an open set

There is no channel registry to validate against:

- `/agents/{id}/interact` takes `channel` as a **free-form query parameter**
  ([`interact/endpoints.py:533`](../../jvagent/action/interact/endpoints.py)),
  so any caller can invent one.
- `normalize_channel` ([`core/channel.py:10`](../../jvagent/core/channel.py))
  only folds `None`/`""`/`web` → `default`; everything else passes through.
- Adapters emit string literals scattered across modules — `whatsapp`
  ([`whatsapp_adapter.py`](../../jvagent/action/whatsapp/whatsapp_adapter.py)),
  `email` ([`email_adapter.py`](../../jvagent/action/email_action/email_adapter.py)),
  `messenger` ([`messenger_adapter.py`](../../jvagent/action/facebook_action/messenger_adapter.py)).
- `whatsapp_call` is not emitted by any adapter literal at all — it arrives from
  jvvoice through the interact endpoint's `channel` parameter, and is known to
  the codebase only as a bare string in
  [`whatsapp_action.py:1527`](../../jvagent/action/whatsapp/whatsapp_action.py)
  (`_OUTBOUND_WA_CHANNELS`) and an orchestrator default.

**Consequence:** an unknown key can never be an error, only an advisory. A custom
deployment with a bespoke channel is legitimate and must not be failed.

### 1.2 The second constraint: warnings currently fail CI

`run_validate` "returns 1 if any warning-level issue is found (suitable for CI)"
([`cli/validate.py:13`](../../jvagent/cli/validate.py)). Every existing
`AgentYamlWarning` is CI-fatal. Adding these checks at that severity would break
the build of any app that has a deliberately-unused override block — an
unacceptable upgrade experience for an advisory lint.

## 2. Goals / non-goals

**Goals**

- Surface a mis-keyed `channel_overrides` block at `jvagent validate` time
  instead of as silent no-op behavior in production.
- Catch the specific `whatsapp` / `whatsapp_call` class of error (Check B).
- Never fail an app for a channel the validator merely doesn't recognize.
- Give the operator a concrete next action, not just "unknown channel".

**Non-goals**

- A closed-world channel registry. Channels stay open by design (§1.1).
- Runtime enforcement or runtime warnings. This is a config-time lint.
- Changing `_channel_cfg` resolution semantics — exact-key matching is correct
  and is now pinned by `tests/action/orchestrator/test_skill_only_channel_overrides.py`.
  Fuzzy/prefix matching is explicitly rejected (§7).
- Validating override *values* (that's the existing per-key validation).

## 3. Locked decisions

| # | Decision | Choice |
|---|---|---|
| 1 | Severity | **New `advisory` severity** that does not fail CI by default. `jvagent validate --strict` escalates advisories to warnings (exit 1). Existing warnings are unchanged. |
| 2 | Known-channel set | Union of three sources (§5.1): a first-party constant, channels declared by enabled actions, and channels referenced elsewhere in the same `agent.yaml`. Open-world: an unrecognized channel is advisory-only. |
| 3 | Check A trigger | An override key not in the known set. Message includes a `difflib` close-match suggestion when one exists. |
| 4 | Check B trigger | An override key sets a knob for one member of a known **channel family** while a sibling member is reachable (its providing action is enabled) and has no block setting that same knob. |
| 5 | Channel families | Declared alongside the first-party constant, seeded with `{whatsapp, whatsapp_call}`. Extensible by actions via the same `provides_channels` declaration. |
| 6 | Which knobs Check B covers | Only knobs whose absence changes behavior silently: `skill_only_tools`, `denied_tools`, `pinned_tools`. Not `history_limit`, ack knobs, or `system_prompt_extra`, where per-channel divergence is normal and intentional. |
| 7 | Action declaration | A new optional `provides_channels: Tuple[str, ...]` class attribute on `Action`, defaulting to `()`. Adapters declare theirs. Purely additive; no action is required to implement it. |

## 4. Worked example

```yaml
# agent.yaml — the incident, reproduced
actions:
  - action: jvagent/orchestrator
    context:
      skill_only_tools: ["payments__*"]
      channel_overrides:
        whatsapp:
          skill_only_tools: []      # meant to ungate voice
```

With `jvagent/whatsapp_voice` enabled, `jvagent validate` emits:

```
advisory  actions[0].context.channel_overrides:
  'skill_only_tools' is overridden for channel 'whatsapp' but not for its
  sibling 'whatsapp_call', which is reachable on this agent (provided by
  WhatsAppVoiceAction). Turns on 'whatsapp_call' will use the action-level
  skill_only_tools instead. Add a 'whatsapp_call' block if that is not intended.
```

And for a genuine typo:

```
advisory  actions[0].context.channel_overrides:
  channel 'whatsap' is not a channel any enabled action provides, and is not
  referenced elsewhere in this agent.yaml. Did you mean 'whatsapp'?
  (Custom channels are supported — this is advisory only.)
```

## 5. Detailed design

### 5.1 The known-channel set

Built per agent, as the union of:

1. **First-party constant** — `KNOWN_CHANNELS` in
   [`jvagent/core/channel.py`](../../jvagent/core/channel.py), the natural home
   (it already owns `normalize_channel`): `default`, `whatsapp`,
   `whatsapp_call`, `email`, `messenger`. This is documentation as much as
   validation — today those strings exist only as scattered literals, which is
   itself part of why the incident happened.
2. **Action-declared** — the union of `provides_channels` over the actions the
   `agent.yaml` enables. This is what makes the check correct for third-party
   adapters rather than only first-party ones.
3. **Self-referenced** — channels named elsewhere in the same `agent.yaml`:
   `voice_ack_channels`, and any skill `allowed-channels` / `denied-channels`
   reachable from this agent. Rationale: an operator who uses a custom channel
   consistently across their config has demonstrated intent, and should not be
   nagged.

Source 3 is what keeps Check A's false-positive rate near zero without a
registry.

### 5.2 Check A — unknown key

For each key in `channel_overrides`, if it is not in the known set, emit an
advisory. Compute a suggestion with `difflib.get_close_matches(key, known, n=1,
cutoff=0.8)`; include it when found. The message must state that custom channels
are supported, so the operator does not "fix" a deliberate custom channel.

Note `normalize_channel` folds `web` → `default`: a block keyed `web` would never
match, since `visitor.channel` is normalized before lookup. Check A should treat
`web` as a **special case with a definite fix** ("use `default`"), not a generic
unknown — it is a guaranteed-dead key, not a maybe.

### 5.3 Check B — sibling coverage

For each channel family (§3 decision #5), for each knob in the covered set
(#6): if some family member's block sets that knob, and another family member is
**reachable** on this agent but has no block setting it, emit an advisory naming
the uncovered sibling and what will happen instead.

"Reachable" means an enabled action declares that channel via
`provides_channels`. This matters: an agent with no voice action should not be
told about `whatsapp_call`. Getting this wrong turns a useful lint into noise,
so the check is deliberately conservative — no declaration, no advisory.

The message must state the *consequence* ("turns on X will use the action-level
value"), not merely the omission. The omission is often correct; the consequence
is what the operator needs in order to judge.

### 5.4 Severity plumbing

`AgentYamlWarning` gains a `severity: str = "warning"` field. `_mk` keeps its
current default so every existing call site is unchanged. `run_validate`
partitions results: warnings → exit 1 as today; advisories → printed, exit
unaffected. `--strict` promotes advisories to warnings.

This is the smallest change that avoids breaking existing CI (§1.2), and it
gives future advisory-grade checks a home rather than forcing each one to choose
between "silent" and "CI-fatal".

## 6. Testing

`tests/core/test_channel_overrides_validation.py`:

1. Unknown key → advisory, not warning; `run_validate` still exits 0
2. Unknown key with a near-miss → advisory includes the suggestion
3. `web` key → advisory naming `default` as the fix
4. Custom channel referenced in `voice_ack_channels` → **no** advisory
5. Custom channel declared by an enabled action's `provides_channels` → no advisory
6. Check B: `skill_only_tools` on `whatsapp`, voice action enabled, no
   `whatsapp_call` block → advisory naming the sibling and the consequence
7. Check B: same config, voice action **not** enabled → no advisory
8. Check B: both siblings set the knob → no advisory
9. Check B: only a non-covered knob (`history_limit`) differs → no advisory
10. `--strict` promotes advisories to exit 1
11. An app with no `channel_overrides` produces no advisories (no regression)

`tests/cli/test_validate_strict.py`: exit-code matrix for warning × advisory ×
`--strict`.

## 7. Alternatives considered

- **Make `_channel_cfg` match prefixes or aliases** (`whatsapp` covers
  `whatsapp_call`). Rejected outright: it would silently change the meaning of
  every existing config, and the separation is deliberate — a voice turn genuinely
  wants different knobs from a chat turn. It is now pinned against by
  `test_override_key_must_match_the_channel_exactly`.
- **Error on unknown keys.** Impossible under §1.1 without breaking custom
  channels, which are a supported deployment shape.
- **Runtime warning on first turn for a channel with no override while a sibling
  has one.** Catches what config-time analysis can't (channels that actually
  occur), but fires in production for a config-time mistake, needs per-process
  dedup state, and says nothing until the miss already happened. Worth
  reconsidering only if the config-time checks prove insufficient in the field.
- **A closed channel registry actions must register into.** The clean long-term
  model, but a breaking change for third-party adapters and far more than this
  problem justifies. `provides_channels` is the additive subset of that idea.
- **Check A alone** (the literal request). Cheap, but would not have caught the
  incident that motivated this — see §1.

## 8. Risks

| Risk | Mitigation |
|---|---|
| Advisory noise trains operators to ignore output | Check B is gated on the sibling being *reachable*; Check A is suppressed by any use of the channel elsewhere in the config. Both fire only on a concrete, actionable mismatch. |
| `KNOWN_CHANNELS` drifts as adapters are added | Source 2 (`provides_channels`) is the real mechanism; the constant is a convenience for first-party strings. A missing entry degrades to an advisory, never a failure. |
| `--strict` in someone's CI turns advisories fatal on upgrade | `--strict` is new and opt-in; default behavior for existing pipelines is unchanged. |
| Check B's family list becomes a maintenance burden | Seeded with one family; extensible through the same action declaration rather than a central list. If no second family materializes, that is evidence the check should stay narrow. |

## 9. As built

Check B shipped; Check A did not. Deltas from §§3–6 above:

1. **Reachability comes from `actions:` refs, not `provides_channels`.**
   `validate_agent_yaml` operates on the parsed YAML dict and never loads Action
   classes, so a class attribute would have been invisible to it. `CHANNEL_PROVIDERS`
   maps channel → the action's published `package.name` instead. Third-party
   adapters are therefore unknown to the lint and produce no advisory — fail-quiet,
   which is the right default for a heuristic.

2. **Provider refs are package names, not directory names.** `jvagent/action/whatsapp_voice/`
   publishes as `jvagent/whatsapp_voice_action`. The first draft used directory
   names, which made the lint silently never fire while every behavioral test still
   passed — the tests used the same wrong constant as their fixtures.
   `test_channel_providers_match_real_action_package_names` now reads the real
   `info.yaml` files and pins the refs; it was mutation-checked against the
   original bug.

3. **The advisory is symmetric.** Gating voice but not chat is flagged the same
   as chat but not voice. §5.3 implied a one-way check; there is no basis for
   treating one direction as more likely.

4. **Dropped:** `KNOWN_CHANNELS`, the difflib suggestion, the `web`→`default`
   special case, and the self-referenced-channel source — all of which existed
   only to serve Check A.

Verified end-to-end against a real app configured with the original incident's
shape: the advisory fires, `jvagent validate` exits 0, `--strict` exits 1. The
bundled example app, which sets `pinned_tools` on both `whatsapp` and
`whatsapp_call`, produces no advisory.
