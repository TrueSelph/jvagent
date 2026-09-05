# Model integration remediation — proposal

**Date:** 2026-09-05 · **Status:** Accepted — Phases 1 (contract, adapter wrap, conformance suite), 2 (LiteLLM adapter, capability registry, `tool_protocol: auto`, context pre-flight, metadata pricing; ADR-0045) and 3 (fallback chain, circuit breaker, retry deadline, budget guard, structured decisions; ADR-0046) landed; Phase 4 pending · **Follows:** [2026-09-05 orchestrator harness audit](../reviews/2026-09-05-orchestrator-harness-audit.md), [ADR-0044](../adr/0044-native-tool-calling-protocol.md)

## 0. One-paragraph answer

The orchestrator now speaks native tool calling (ADR-0044), but it speaks it to a
hand-rolled model layer: ~2,700 lines of per-provider httpx clients, SSE parsers
and tool-call assemblers, a hand-maintained price table with four models, and no
notion of what a given model can do. Every provider quirk is a bug we own. The
remedy is not another rewrite of that layer — it is to **stop owning the
provider wire format**: put a small normalised contract in front of the model
layer, back it with **LiteLLM as the default adapter** (100+ providers, tool
calling, streaming, pricing and capability metadata maintained upstream, and
already a dependency via PageIndex), keep first-party OpenAI/Anthropic adapters
only while a conformance suite says LiteLLM cannot yet match them, and add the
three harness-level pieces no library gives us: a per-model **capability
registry**, a **fallback chain with circuit breaking**, and **conformance tests
recorded against real providers**.

---

## 1. Where the model layer is today

| File | Lines | What it hand-rolls |
|---|---|---|
| `model/language/openai/openai.py` | 761 | payload building, SSE parsing, streaming tool-call buffer assembly, reasoning-model parameter shaping, usage/cache-token extraction, error decoding |
| `model/language/anthropic/anthropic.py` | 711 | OpenAI→Anthropic message/tool/role normalisation, `cache_control`, extended thinking, SSE parsing, tool-call assembly |
| `model/language/ollama/ollama.py` | 491 | NDJSON parsing, tool-call normalisation both directions, `format: json` |
| `openrouter.py`, `groq.py` | 322 | OpenAI subclasses with per-vendor patches |
| `model/base.py` | 535 | retries (Retry-After, jitter), per-loop httpx client, usage tracking |
| `model/cost_estimator.py` | 162 | **hard-coded prices for 4 models**; cache-read/write multipliers |
| `model/utils/token_estimation.py` | 201 | tiktoken estimates when providers omit usage |
| `model/context.py` | 259 | per-turn override, model slots (heavy/light), gear binding |

Verified gaps (audit M1–M7 plus this pass):

1. **Three SSE/NDJSON parsers and three tool-call assemblers** with subtly
   different behaviour (Anthropic emits `max_tokens`, OpenAI `length`; Ollama
   synthesises call ids; only OpenAI reports cached tokens as a nested field).
   ADR-0044 had to normalise `finish_reason` in the orchestrator because the
   adapters do not.
2. **No capability model.** Nothing tells the orchestrator whether a model
   supports tool calling, parallel calls, JSON mode, vision, thinking, or what
   its context window / output ceiling is. `tool_protocol` is a manual switch;
   `reasoning_model_patterns` is a regex list; `model_max_tokens=4096` is a
   guess applied to every model.
3. **Pricing is stale by construction.** Four OpenAI SKUs and two Claude
   versions; everything else bills at zero. Cost policy (ADR-0041) is built on
   numbers that are mostly missing.
4. **No fallback, no breaker.** A provider outage is now reported honestly
   (ADR-0044 `model_error`) but the turn still fails. There is no "try the light
   model on the other provider", and a dead provider is retried on every turn.
5. **Retries are not idempotent-safe and have no total deadline** (2026-09-01
   review MEDIUMs, still open): a POST retried on timeout can double-bill; worst
   case ~15 minutes.
6. **JSON protocol has no structured-output path.** Where a model lacks tool
   calling we fall back to prompt-obedience JSON; OpenAI `json_schema` and
   Anthropic forced-tool structured output are unused.
7. **No provider conformance tests.** `tests/action/model/` mocks httpx per
   provider; nothing asserts that all adapters produce the *same* normalised
   result for the same logical exchange, and nothing runs against a real
   endpoint.
8. **Two model stacks in one repo.** PageIndex already calls `litellm`
   directly (`pageindex/core/utils.py`, `local_api.py`), so the dependency,
   its import cost and its config surface are already paid for — but only
   half the codebase benefits.

---

## 2. Target shape

```
Orchestrator / ReplyAction / interview engine / PageIndex
                │  ModelRequest → ModelResponse   (ONE normalised contract)
                ▼
      LanguageModelAction (jvspatial node: config, slots, overrides,
                           observability, cost policy, breaker, fallback)
                │  provider-neutral call
                ▼
      ModelAdapter (protocol) ──┬── LiteLLMAdapter      ← default, all providers
                                ├── OpenAIAdapter       ← first-party, kept while needed
                                └── AnthropicAdapter    ← first-party, kept while needed
```

- **`ModelRequest` / `ModelResponse`** are the only types the harness sees:
  messages (OpenAI-shaped, tool roles included), tools (JSON-Schema functions),
  `tool_choice`, `parallel_tool_calls`, `response_format`, reasoning config,
  max_tokens; back come `text`, `tool_calls[{id,name,arguments:dict}]`,
  `finish_reason ∈ {stop, tool_calls, length, content_filter, error}`, `usage`
  (prompt/completion/cached_read/cached_write/thinking), `thinking`, provider
  request id, latency. `ModelActionResult` becomes this plus the stream.
- **`ModelAdapter`** is a small protocol: `complete(request) -> ModelResponse`,
  `stream(request) -> AsyncIterator[Delta]`, `capabilities(model) ->
  ModelCapabilities`, `count_tokens(messages, model) -> int`,
  `price(model) -> Pricing | None`.
- **`LanguageModelAction`** keeps everything jvagent-specific and loses
  everything provider-specific: config attributes, slot/gear resolution,
  per-turn overrides, `model_call` observability events, cost accounting,
  retries/breaker/fallback policy. Provider subclasses collapse to "which
  adapter, which credentials, which default model".

---

## 3. Options

| | A. Status quo + patch | B. Official SDKs per provider | C. LiteLLM everywhere | **D. Contract + LiteLLM default + conformance-gated first-party (recommended)** |
|---|---|---|---|---|
| Wire formats owned by us | all | none for OpenAI/Anthropic; Ollama/Groq/OpenRouter still ours | none | none by default; only where conformance proves a gap |
| Provider breadth | 5 | 5 | 100+ (Gemini, Bedrock, Azure, Mistral, Vertex, Together, …) | 100+ |
| Capability + pricing metadata | hand-maintained | none | `get_model_info`, `supports_function_calling`, `model_cost` maintained upstream | upstream, cached, overridable |
| Anthropic `cache_control`, thinking, cache-token usage | ours | native | supported (passthrough + normalised usage) | supported; conformance test asserts parity |
| Dependency weight / import time | none | 2 SDKs | large (LiteLLM pulls openai, tiktoken, tokenizers, jinja2, …; import ~1 s) — **already paid via PageIndex** | same as C, gated behind the existing `litellm` extra; lazy import |
| Behavioural drift risk | ours to find | low | upstream releases move fast; pin + conformance suite | pinned, conformance-gated upgrades |
| Effort | ongoing | medium | medium | medium, incremental, reversible per provider |

Why not C outright: LiteLLM is a translation layer, not a harness. It will not
give us jvagent's slot/gear resolution, per-turn overrides, the `model_call`
observability contract, the egress scrub, or the breaker/fallback *policy* we
want expressed in `agent.yaml`. And we should not depend on it being perfect for
the two providers that carry production traffic today until a test says so.

Why not B: it fixes robustness for two providers and leaves breadth, capability
metadata and pricing exactly where they are.

---

## 4. Plan (four phases, each independently shippable)

### Phase 1 — Contract and conformance (no behaviour change)

1. Add `jvagent/action/model/contract.py`: `ModelRequest`, `ModelResponse`,
   `ToolCall`, `Usage`, `ModelCapabilities`, `Pricing`, and the `ModelAdapter`
   protocol. Pure dataclasses, no provider imports.
2. Wrap the three existing provider actions as adapters behind the protocol
   *without* moving code: `_query` / `_query_stream` produce `ModelResponse`.
   Normalise `finish_reason` and usage keys here (remove the ADR-0044
   normalisation from the orchestrator).
3. **Conformance suite** `tests/action/model/conformance/`: one parametrised
   test module run against every adapter over recorded fixtures — plain text,
   single tool call, parallel tool calls, tool result round-trip, streaming
   with tool-call assembly, `length` truncation, 429 with Retry-After, 5xx,
   malformed JSON body, usage with cached tokens, thinking blocks, image
   content. Fixtures are recorded once per provider with a `--record` flag
   against real endpoints (keys from env, skipped otherwise) and replayed in CI.
   This is the instrument that makes every later step safe.
4. Orchestrator and ReplyAction consume `ModelResponse` only.

### Phase 2 — LiteLLM adapter and metadata

5. `LiteLLMAdapter` + `LiteLLMLanguageModelAction` (`jvagent/litellm_lm`),
   extra `jvagent[litellm]` (already declared for tests). Lazy import; a boot
   warning, not a crash, when the extra is missing and the action is enabled.
   Model id in LiteLLM form (`anthropic/claude-…`, `bedrock/…`, `ollama/…`).
   Pass through `cache_control`, thinking, `tool_choice`,
   `parallel_tool_calls`, `response_format`; take `usage` including
   `prompt_tokens_details.cached_tokens` / `cache_read_input_tokens` from its
   normalised response.
6. **Capability registry** `jvagent/action/model/capabilities.py`: per
   model id → `supports_tools`, `supports_parallel_tools`, `supports_json_mode`,
   `supports_structured_output`, `supports_vision`, `supports_thinking`,
   `context_window`, `max_output_tokens`. Source order: `agent.yaml` override
   → LiteLLM `get_model_info` (when installed) → a small bundled table for the
   five first-party providers → conservative defaults. Consumers:
   - orchestrator `tool_protocol: auto` (new default): native when
     `supports_tools`, else `json`; `parallel_tool_calls` only when supported;
   - `model_max_tokens` clamped to `max_output_tokens`;
   - a **pre-flight context budget**: count prompt tokens, and when over
     `context_window − max_output`, tighten `max_observations_in_prompt` /
     `history_limit` for that tick instead of letting the provider 400.
7. **Pricing from metadata**: `cost_estimator` reads `Pricing` from the adapter
   (LiteLLM `model_cost` when present, bundled table otherwise); the four-model
   dict goes away. Cost events unchanged.
8. Run the conformance suite against `LiteLLMAdapter` for OpenAI, Anthropic,
   Ollama, Groq, OpenRouter, plus Gemini and Bedrock. Publish the parity
   matrix in `docs/language-models.md`.

### Phase 3 — Resilience policy (harness-owned, adapter-agnostic)

9. **Fallback chain** in `agent.yaml`: `model_fallbacks: [{model, action_type}]`
   per slot (heavy/light). On `model_error` after the adapter's retries, the
   `LanguageModelAction` tries the next entry within the same tick; the
   orchestrator only sees success or a final `model_error`. Recorded in the
   `model_call` event (`fallback_from`).
10. **Circuit breaker** per (provider, model): N consecutive failures open it
    for T seconds; open circuits are skipped in the chain; state per event
    loop (serverless-safe, `core/app.py` lock pattern). Health surfaces in
    `jvagent status`.
11. **Retry hygiene**: total retry deadline (e.g. 60 s), idempotency key header
    where the provider supports it (OpenAI), no retry on non-idempotent
    timeouts without it — closes the 2026-09-01 MEDIUMs.
12. **Budget guard**: per-conversation and per-turn cost ceilings using the
    now-real pricing; exceeding a turn ceiling ends the loop with a typed
    `budget_exhausted` decision and the partial-compose path.
13. **Structured-output fallback** for the JSON protocol: when
    `supports_structured_output`, send the decision schema as
    `response_format: json_schema` (OpenAI) / forced tool (Anthropic) so
    even non-tool-calling paths get provider-validated decisions.

### Phase 4 — Consolidate

14. With the parity matrix green, deprecate `OpenAILanguageModelAction` /
    `AnthropicLanguageModelAction` internals to thin subclasses of the LiteLLM
    action (same class names, same `agent.yaml`, same credentials — zero
    operator change). Delete the hand-rolled SSE parsers and tool assemblers
    (~1,500 lines). Keep the classes as compatibility shims for one minor
    release, then fold.
15. Move PageIndex's direct `litellm` calls onto the same `LanguageModelAction`
    so one stack, one observability event, one cost ledger.
16. **Nightly live CUCS** (existing `LiveScenarioRunner`) against one model per
    provider on the native protocol; failures open an issue, not a red PR.

---

## 5. Acceptance criteria ("robust harness" made testable)

- Every adapter passes the same conformance suite; a provider bug becomes a
  failing recorded fixture, not a production incident.
- `tool_protocol: auto` picks native/json from capabilities; no agent needs the
  manual switch for a mainstream model.
- A provider outage produces, in order: adapter retries → fallback model →
  `model_unavailable_text`; never `clarify_text`, never a hung turn, and the
  breaker stops re-trying a dead provider on every turn.
- Cost events carry a real price for every model in the parity matrix.
- A prompt that would exceed the context window is trimmed before the call.
- Adding a provider is configuration (a LiteLLM model id) plus one conformance
  recording — no new Python adapter.

---

## 6. Risks and mitigations

| Risk | Mitigation |
|---|---|
| LiteLLM release churn breaks a mapping | pin in `pyproject`, conformance suite gates upgrades, first-party adapters stay until parity is proven per provider |
| Import time / cold start on Lambda | lazy import inside the adapter; measure in `scripts/bench_orchestrator.py`; PageIndex already carries the cost |
| Feature LiteLLM lacks (a new Anthropic beta header, a provider-specific field) | `extra_body` / header passthrough on `ModelRequest`; first-party adapter for that provider stays behind the same contract |
| Capability metadata wrong for a model | `agent.yaml` override wins; `jvagent validate` prints the resolved capabilities |
| Two truths during migration | Phase 1 contract is the single consumer surface from day one; adapters are swapped underneath |

---

## 7. Sequencing and size

| Phase | Scope | Size | Dependency |
|---|---|---|---|
| 1 | contract, adapter wrap, conformance suite, orchestrator/reply on contract | ~1 week | none (pure refactor + tests) |
| 2 | LiteLLM adapter, capability registry, pricing, `tool_protocol: auto`, parity matrix | ~1 week | 1 |
| 3 | fallback chain, breaker, retry hygiene, budget guard, structured-output fallback | ~1 week | 1 (2 for metadata-driven parts) |
| 4 | fold first-party adapters, PageIndex onto the stack, nightly live CUCS | ~3 days | 2, 3 |

Phases 1 and 3 are valuable even if LiteLLM is ultimately rejected; Phase 2 is
where the decision is made on evidence (the parity matrix), not on a slide.
