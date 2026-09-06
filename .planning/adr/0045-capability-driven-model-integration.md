# ADR 0045 — Capability-driven model integration (LiteLLM adapter, capability registry)

**Status**: Accepted
**Date**: 2026-09-05
**Relation**: Phase 2 of [`.planning/specs/2026-09-05-model-integration-remediation.md`](../specs/2026-09-05-model-integration-remediation.md). Builds on the normalised contract (Phase 1) and refines [ADR-0044](0044-native-tool-calling-protocol.md) (the `native` default becomes `auto`). Extends [ADR-0041](0041-gearing-and-cost-policy-in-core.md) (cost policy needs real prices).

---

## 1. Context

After ADR-0044 the Orchestrator speaks native tool calling, but three things
about the model it speaks to are still guessed:

1. **What the model can do.** `tool_protocol` was a manual switch; the loop
   sent `parallel_tool_calls=False` to every provider; `model_max_tokens=4096`
   applied to every model; nothing knew a context window, so an oversized
   prompt failed at the provider mid-turn.
2. **What a call costs.** `cost_estimator.py` priced four OpenAI SKUs and two
   Claude versions; everything else billed at zero or a flat default, so the
   cost policy of ADR-0041 ran on mostly-missing numbers.
3. **Which providers exist.** Five hand-rolled httpx adapters; adding a provider
   meant a new parser, a new tool-call assembler and a new set of quirks to own.
   PageIndex already called `litellm` directly, so the dependency was paid for
   by half the codebase.

## 2. Decision

### 2.1 Capability registry

`jvagent/action/model/capabilities.py` resolves a `ModelCapabilities` for any
`(model, provider)` — tool calling, parallel calls, JSON mode / structured
output, vision, thinking, context window, output ceiling — field by field,
first known value wins:

1. operator override — `model_capabilities` on the language-model action
   (`agent.yaml`);
2. LiteLLM metadata — `litellm.get_model_info` when the optional extra is
   installed (maintained upstream, cached per process);
3. a bundled table for the families the first-party adapters ship with;
4. **unknown** (`None`). Nothing is guessed.

`LanguageModelAction.capabilities()` and `.pricing()` (Phase 1 stubs) are
backed by it. Consumers treat unknown as "mainstream default", never as a
capability claim.

### 2.2 The Orchestrator reads capabilities, not config

- `tool_protocol: auto` (new default): `json` only when the model is **known**
  not to support tool calling; native otherwise (including unknown). Resolved
  once per turn and cached on the turn so every prompt piece agrees; recorded
  as `tool_protocol` on the activation event.
- `parallel_tool_calls=False` is sent only when the provider is not known to
  lack the parameter.
- `model_max_tokens` is clamped to the model's output ceiling.
- **Context pre-flight**: with a known context window, the assembled request is
  token-estimated and trimmed — oldest history exchanges first, then the
  observation replay (fewer, then smaller, results, down to a floor) — until
  it fits `0.95 × window − max_tokens`. Trims are logged and recorded
  (`context_trims`). With an unknown window nothing changes.

### 2.3 Pricing from metadata

`cost_estimator.pricing_for(provider, model)` returns LiteLLM pricing (per
million, with cache read/write multiplers derived from upstream per-token
costs) when available, else the bundled table, else `None`. `estimate_cost`
uses it first; the flat default remains the last resort. Cached prompt tokens
are read from the raw OpenAI `prompt_tokens_details` as well as the flattened
key.

### 2.4 LiteLLM as the universal adapter

`jvagent/litellm_lm` (`LiteLLMLanguageModelAction`) implements the Phase 1
contract over `litellm.acompletion`: LiteLLM-form model ids
(`anthropic/claude-sonnet-4-5`, `bedrock/…`, `ollama/…`), provider credentials
from the usual environment variables or `api_key` / `api_base`, tool calling,
streaming (assembled with `stream_chunk_builder`), `drop_params` so a provider
that lacks a parameter does not fail the call, and `num_retries=0` because the
harness owns retries. `BaseModelAction._is_retryable_exception` now reads a
`status_code` attribute so SDK-style exceptions retry like httpx ones. It is
an optional extra (`jvagent[litellm]`), imported lazily, with a clear error when
absent. `provider: litellm` joins the slot/override mapping.

### 2.5 Conformance gates the claim

The Phase 1 conformance suite runs the LiteLLM adapter through the same twelve
scenarios as the first-party adapters (fed the OpenAI wire bodies through its
`_acompletion` seam, building real `litellm` response objects), and the same
normalised `ModelResponse` is asserted. The parity matrix lives in
`docs/language-models.md`.

## 3. Consequences

- **Behaviour change on upgrade**: `tool_protocol` defaults to `auto`. For every
  mainstream model this resolves to `native` (as before); a model the registry
  knows cannot call tools now gets the JSON contract automatically. Agents that
  pinned `native`/`json` are unchanged.
- Long turns on small-window models no longer fail at the provider; they lose
  the oldest history and the largest replayed results first, and say so.
- Cost events carry a real price for every model LiteLLM knows.
- First-party OpenAI/Anthropic/Ollama adapters stay. Folding them into thin
  shims over LiteLLM (Phase 4) is decided on the parity matrix, not here.
- The registry's bundled table is a maintenance surface; the override key is
  the escape hatch when it (or upstream) is wrong for a model.

## 4. Alternatives considered

- **LiteLLM for calls only, no registry.** Leaves `tool_protocol` and budgets
  as guesses for the first-party adapters that carry production traffic today.
- **Hard-fail on unknown capabilities.** Rejected: unknown must degrade to the
  mainstream default, or every new model needs a table entry before it works.
- **Truncate the prompt at the provider's error.** Rejected: the failure arrives
  after the turn has spent its ticks; pre-flight keeps the turn alive.
