# ADR 0049 — SESSION CONTEXT placement and prompt-cache telemetry

**Status**: Accepted
**Date**: 2026-09-06
**Relation**: Amends the placement decided in [ADR-0042](0042-session-context-ground-truth.md) (content and authority of the block are unchanged). Follow-up from [`.planning/reviews/2026-09-05-example-live-evaluation.md`](../reviews/2026-09-05-example-live-evaluation.md) §2.3 ("zero prompt-cache hits — measure before changing").

---

## 1. Context

The live evaluation could not say whether the Orchestrator ever hit a
provider prompt cache: the adapters reported cached prompt tokens
(`cached_tokens` from OpenAI and LiteLLM, `cache_read_input_tokens` from
Anthropic) but two copies of the usage dict on the way to the `model_call`
event kept only the three totals, so telemetry showed nothing either way and
the cost estimate never applied the cache discount.

Once the counts were carried through, the measurement was unambiguous:

| Scenario (gpt-4.1 via LiteLLM, example agent) | Prompt tokens | Cached | Why |
|---|---|---|---|
| Identical first turn in two sessions, block after identity | 3,584 | **0** | prompts diverge at character ~220 (the clock), under the 1,024-token cache minimum |
| Second tick of the same turn | 4,718 | **0** recorded | provider cached 2,560 of it — the count was dropped in the harness |

The block ADR-0042 placed "immediately after identity" as *cacheable ground
truth* was the one thing in the prompt that changes every turn, and it sat in
front of ~11,000 stable characters.

## 2. Decision

1. **Carry the cache breakdowns through.** `USAGE_BREAKDOWN_KEYS`
   (`cached_tokens`, `cache_read_input_tokens`, `cache_creation_input_tokens`,
   `reasoning_tokens`, `thinking_tokens`) ride beside the three totals in
   `BaseModelAction.track_usage` and `LanguageModelAction.query_messages`, so
   the `model_call` event carries them. `Interaction.compute_usage` aggregates
   `cached_prompt_tokens` and `cache_write_tokens`; `estimate_cost` already
   priced cached reads at the provider's discount and now sees them.
2. **Render SESSION CONTEXT last.** The built-in template
   (`ORCHESTRATOR_STABLE_SYSTEM_PROMPT`) places `{session_context_section}`
   after the operating rules: identity, protocol, extras, capabilities,
   skills, tools and rules first — stable across turns for an agent — then
   the per-turn clock and channel. The block's wording ("authoritative for
   this turn", "relative time MUST use this clock") is unchanged; the
   grounding corpus (ADR-0048 follow-up in #189) reads it from the prompt
   cache, not from its position.
3. **A persisted copy of a past built-in is still the default.**
   `system_prompt` is stored on the action node at bootstrap, so the new
   layout would otherwise reach a running deployment only after a source-mode
   sync — the first live check after the change still rendered the old
   layout. `_compose_system_prompt` now swaps any template that matches a
   past built-in (`BUILTIN_SYSTEM_PROMPT_HISTORY`, plus the JSON-era prompt,
   compared through the store's fold) for the current one. An operator's own
   template never matches and keeps whatever slot position it chose;
   templates without the slot already appended the block last.

## 3. Consequences

Measured on the same turns after the change (same model, same route):

| Scenario | Prompt tokens | Cached | Turn cost |
|---|---|---|---|
| Identical first turn, new session | 3,584 | **1,920** | $0.0073 → **$0.0044** |
| Second tick of the same turn | 4,505 | **4,352** | — |
| Sixth tick of a research turn | 6,707 | **6,400** | — |

- Cross-turn and cross-session hits exist now; within-turn hits are near
  total. The remaining uncached share on a first turn is the block itself,
  the user turn, and whatever precedes the first cache boundary the provider
  chooses.
- Anything an operator appends to the system prompt *after* compose
  (`LENGTH LIMIT`, channel extras) still lands after the block; that text is
  stable, so it costs at most one cache chunk. Moving it above the block is
  a possible refinement, not required.
- The pre-0049 template is kept verbatim in `prompts.py` as
  `ORCHESTRATOR_SYSTEM_PROMPT_PRE_0049`. Every future change to the built-in
  template must append the previous version to `BUILTIN_SYSTEM_PROMPT_HISTORY`,
  or upgraded deployments keep rendering the version they were bootstrapped
  with.
- No YAML knob. No change to what the model is told, only where.

## 4. Verification

- `tests/memory/test_usage.py`: aggregation of cached reads/writes and the
  discounted estimate.
- `tests/action/model/test_transport.py`: `cached_tokens` survives
  `query_messages` → `track_usage` → the event usage.
- `tests/action/orchestrator/test_session_context.py`: block renders last;
  a persisted pre-0049 template (raw and store-folded) renders the current
  layout; an operator template keeps its own slot position.
- `tests/wire/test_prompt_contract.py`: the same on an orchestrator loaded
  back out of a real graph.
- Live: the table above, from the example agent's `model_call` telemetry.
