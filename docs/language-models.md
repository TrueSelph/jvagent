# Language model actions (HTTP retries)

`BaseModelAction` (including all `LanguageModelAction` providers: OpenAI, Ollama, Anthropic, OpenRouter) supports automatic retries for **transient** HTTP failures so a single timeout or connection blip does not abort long-running flows (e.g. the Orchestrator think-act-observe loop).

## Defaults

| Setting | Default | Meaning |
|--------|---------|--------|
| `max_retries` | `2` | Extra attempts after the first failure (`0` = no retries; up to `max_retries + 1` total attempts). |
| `retry_initial_delay` | `1.0` | Base delay in seconds before the first retry. |
| `retry_max_delay` | `20.0` | Cap on backoff delay. |
| `retry_backoff_multiplier` | `2.0` | Multiplier applied each attempt (exponential backoff). |
| `retry_jitter` | `true` | Randomize delay between `0.5×` and `1.5×` the computed delay. |
| `retry_on_status_codes` | `408, 425, 429, 500, 502, 503, 504` | `httpx.HTTPStatusError` codes that trigger a retry. |
| `timeout` | `120` | HTTP client request timeout in seconds (reasoning models often need longer runs). |

Retries apply to:

- `httpx.TimeoutException` (including read/connect timeouts)
- `httpx.TransportError` (connection resets, TLS issues, etc.)
- `httpx.HTTPStatusError` when the response status is in `retry_on_status_codes`

For **429** and **503**, if the response includes a `Retry-After` header (seconds or HTTP-date), the wait time uses that value (capped by `retry_max_delay`, then jitter if enabled).

**Not** retried: other `HTTPStatusError` (e.g. 401/403/404), `asyncio.CancelledError`, or arbitrary non-httpx exceptions.

## Sync vs streaming

- **Non-streaming** (`query` / `query_messages` with `stream=False`): the full request is retried via `_execute_with_retry`.
- **Streaming** (`stream=True`): the initial `_query_stream` call is retried; if the stream fails **before the first chunk**, the call is retried up to `max_retries` times. After at least one chunk has been yielded, failures are **not** retried (avoids duplicate or partial output).

## Configuration

Override per action in `agent.yaml` under the model action’s `context`:

```yaml
- action: jvagent/ollama_lm
  context:
    timeout: 120
    max_retries: 3
    retry_initial_delay: 2.0
    retry_max_delay: 30.0
    retry_jitter: true
```

See also [configuration.md](configuration.md) for general resolution order (defaults → `agent.yaml` context).

## Reasoning models (OpenAI)

Native OpenAI **reasoning** / “thinking” models (e.g. `o1*`, `o3*`, `o4-mini`, `gpt-5*`) use different Chat Completions fields than standard chat models:

- **`max_completion_tokens`** is sent instead of **`max_tokens`**.
- **`temperature`** and **`top_p`** are omitted (only the default sampling is accepted).
- **`reasoning_effort`** is sent as a **top-level** string (`minimal`, `low`, `medium`, `high`), not as a nested `reasoning` object.

`OpenAILanguageModelAction` **auto-detects** reasoning models by matching the model id against `reasoning_model_patterns` (regex list, overridable in `agent.yaml`). To force behavior regardless of the model name, set **`is_reasoning_model: true`** or **`is_reasoning_model: false`** on the model action’s `context`.

Configure effort in either of these ways:

1. **`reasoning_effort`** on `jvagent/openai_lm` or on the model action backing a center (recommended for OpenAI).
2. Legacy nested form **`reasoning: { effort: medium }`** on the model action — it is translated to top-level `reasoning_effort` for native OpenAI only.

**OpenRouter** (`OpenRouterLanguageModelAction`) does **not** apply this reshaping: it keeps the nested `reasoning: { effort: ... }` body OpenRouter expects. Use the `reasoning` dict there as before.

Example (`agent.yaml`):

```yaml
- action: jvagent/openai_lm
  context:
    model: o3-mini
    timeout: 180
    reasoning_effort: medium
- action: jvagent/orchestrator
  context:
    model_action_type: OpenAILanguageModelAction
    model: gpt-5.1-2025-11-13
    reasoning_effort: low
```

## The normalised contract (`jvagent.action.model.contract`)

Consumers read one shape, whatever the provider: [`ModelRequest`](../jvagent/action/model/contract.py)
in, [`ModelResponse`](../jvagent/action/model/contract.py) out.

```python
from jvagent.action.model.contract import ModelRequest

response = await model_action.complete(
    ModelRequest(messages=messages, tools=tools, tool_choice="auto")
)
response.text            # str
response.tool_calls      # [ToolCall(id, name, arguments: dict, raw_arguments)]
response.finish_reason   # "stop" | "tool_calls" | "length" | "content_filter" | "error" | "unknown"
response.usage           # Usage(prompt, completion, total, cached_read, cached_write, thinking, estimated)
response.thinking        # provider reasoning text when present
response.truncated       # finish_reason == "length"
```

Provider quirks are normalised in the contract, not at call sites: Anthropic
`max_tokens` / `end_turn` / `tool_use` and Ollama `length` / `stop` map onto
the same finish reasons (a response carrying tool calls is `tool_calls` even
when the provider labelled it `stop`); OpenAI `prompt_tokens_details.cached_tokens`
and Anthropic `cache_read_input_tokens` both land in `usage.cached_read_tokens`;
tool-call arguments are parsed to a dict with the raw string kept for the
unparseable case. `ModelActionResult.to_response()` returns the same object, so
existing `query_messages()` callers can migrate one read at a time. The
Orchestrator already consumes only the contract.

`capabilities(model)` and `pricing(model)` are declared on every language-model
action; in this phase capabilities are all "unknown" (never guessed) and pricing
comes from the bundled table. Phase 2 of the
[remediation plan](../.planning/specs/2026-09-05-model-integration-remediation.md)
populates both from provider metadata.

### Capabilities and pricing (ADR-0045)

`capabilities(model)` resolves what a model can do — `supports_tools`,
`supports_parallel_tools`, `supports_json_mode`, `supports_structured_output`,
`supports_vision`, `supports_thinking`, `context_window`, `max_output_tokens` —
field by field, first known value wins:

1. `model_capabilities` on the language-model action (`agent.yaml` override);
2. LiteLLM metadata (`litellm.get_model_info`) when the `litellm` extra is installed;
3. a bundled table for the mainstream families (GPT-4o/4.1/5, o-series, Claude 3.x/4.x, common open models);
4. unknown (`None`) — never guessed.

The Orchestrator consumes them: `tool_protocol: auto` picks `json` only for a
model known not to call tools; `parallel_tool_calls` is withheld from providers
known to lack it; `model_max_tokens` is clamped to the output ceiling; and the
**context pre-flight** trims oldest history, then observation replay, until the
estimated request fits `0.95 × context_window − max_tokens` (recorded as
`context_trims` on the activation event). Override a wrong entry per agent:

```yaml
  - action: jvagent/ollama_lm
    context:
      model: my-finetune
      model_capabilities: { supports_tools: false, context_window: 8192 }
```

`pricing(model)` (and `cost_estimator.estimate_cost`) use LiteLLM's upstream
price table when available, the bundled table otherwise — cost events carry a
real price for every model LiteLLM knows.

### LiteLLM universal adapter (`jvagent/litellm_lm`)

One action for every provider LiteLLM speaks — OpenAI, Anthropic, Gemini,
Bedrock, Azure, Mistral, Groq, OpenRouter, Ollama, … — behind the same contract:

```yaml
  - action: jvagent/litellm_lm
    context:
      enabled: true
      model: anthropic/claude-sonnet-4-5     # LiteLLM provider/model id
      # api_key: ...                          # else the provider's env var (ANTHROPIC_API_KEY)
      # api_base: http://localhost:11434      # e.g. ollama/… against a local host
      # drop_params: true                     # drop parameters the provider lacks
```

Install the extra: `pip install "jvagent[litellm]"`. The import is lazy — an
install without it boots and the action raises a clear error on first use. The
extra pins litellm to **one minor** (`>=X.Y.Z,<X.Y+1`, identical in
`pyproject.toml`, `requirements-all.txt` and both action `info.yaml` files —
enforced by `tests/test_requirements_sync.py`): litellm releases near-daily and
had a PyPI supply-chain compromise in March 2026, so the fleet moves versions
deliberately — Dependabot proposes the bump, `tests/action/model` and
`scripts/live_smoke.py --provider litellm` gate it. The
harness owns retries (`num_retries=0` is sent; `BaseModelAction` retries on the
exception's `status_code`), streaming is assembled with
`litellm.stream_chunk_builder`, and `provider: litellm` works in slot overrides.
Reference the class as `LiteLLMLanguageModelAction` in `model_action_type`.

**Ollama through LiteLLM.** An `ollama/<model>` id is sent to LiteLLM's
`ollama_chat/` provider — the `/api/chat` route with native tool calling
(Ollama ≥ 0.4). LiteLLM's `ollama/` provider is the `/api/generate` route: it
emulates tool calling by parsing JSON out of the content, and a call the model
phrases differently arrives as prose (issue #203). `litellm_ollama_route:
generate` on the adapter opts out. Note LiteLLM *infers* `supports_function_calling`
for Ollama models from the model template, which cloud models do not expose;
jvagent treats that inferred `False` as unknown and probes native first,
demoting to the JSON contract only if the provider refuses (ADR-0051).

### Transport switch (ADR-0047)

Every first-party action can route its calls through the LiteLLM adapter
without changing class, `agent.yaml` or credentials:

```yaml
  - action: jvagent/openai_lm
    context:
      model: gpt-4o-mini
      transport: litellm        # default httpx; JVAGENT_MODEL_TRANSPORT=litellm overrides fleet-wide
```

The action hands the delegate its own model (`openai/gpt-4o-mini`), API key and
non-default endpoint (`litellm_model_id()` / `litellm_call_config()`), and
relabels the result with its own provider and model, so cost events, slot
resolution and telemetry read exactly as before. Retries, breaker, fallbacks
and budgets apply to both transports. Known gap under `litellm`: Anthropic
`cache_control` breakpoints are not applied. The conformance matrix below runs
each first-party adapter under both transports; the nightly live workflow runs
the smoke scenarios on both. Deleting the own-wire clients is gated on that
evidence (ADR-0047 §2.3).

### Migrating a deployed agent to LiteLLM

The example app's agents run their orchestrator through the adapter
(`examples/jvagent_app/agents/jvagent/orchestrator_agent/agent.yaml`). Moving a
deployed agent is a two-step, reversible change; do the steps in order and
verify between them.

1. **Install the extra on the deployment**: `pip install "jvagent[litellm]"`
   (or add `litellm` to the image). Without it the server still boots; the
   first model call through the adapter raises a clear error.
2. **Flip the transport first, class second.** Set `transport: litellm` on the
   agent's existing first-party model action (`jvagent/openai_lm`,
   `jvagent/anthropic_lm`, …). Nothing else changes — same class, model id,
   key and endpoint — so cost events, slot overrides and telemetry read as
   before while every call goes through LiteLLM. Run the agent's normal
   traffic for a while; a regression here is LiteLLM's wire, not your config.
3. **Then point the orchestrator at the adapter.** Add `jvagent/litellm_lm`
   (enabled) to the agent's actions and set the orchestrator's slots to
   LiteLLM ids: `model: openai/gpt-4.1`, `model_action_type:
   LiteLLMLanguageModelAction` (and the `light_model*` pair if gearing is on).
   From here changing provider is changing the id (`anthropic/…`, `gemini/…`,
   `bedrock/…`) plus the provider's key in the environment; capabilities and
   pricing follow from LiteLLM's metadata (ADR-0045), so `tool_protocol: auto`
   picks native tool calling where the model supports it.
   Run step 2 as a **canary** first — one deployment on
   `JVAGENT_MODEL_TRANSPORT=litellm`, the rest unchanged, compared over a few
   days: [`.planning/runbooks/litellm-canary.md`](../.planning/runbooks/litellm-canary.md).
4. **Turn on the resilience you want** once the new path is steady:
   `model_fallbacks` (same adapter, different id), `circuit_breaker_failures`,
   `max_turn_cost_usd` (ADR-0046). All default off except the breaker.

**Sync the graph after each edit — in source mode.** Every `attribute` is
persisted on the action node at bootstrap, so editing `agent.yaml` changes
nothing on a running deployment until you sync. Merge mode (`--update`) adds
new actions and new keys but **keeps existing values**, so it will register
`jvagent/litellm_lm` yet leave the orchestrator's `model_action_type` on the
old class — the live check of exactly this migration ran four turns on the
first-party wire after a merge sync before anyone noticed. Use

```bash
jvagent <app> --update --source --yes     # or JVAGENT_ASSUME_YES=1; prompts otherwise
```

which replaces action config from YAML (conversation memory is untouched —
the example's interaction count was identical before and after), then confirm
on the persisted node or in the `model_call` telemetry (`provider: litellm`).
See "Persisted config outruns your edits" in [ORCHESTRATOR.md](ORCHESTRATOR.md).

**Rollback** is the same edit in reverse (`model: gpt-4.1`,
`model_action_type: OpenAILanguageModelAction`, drop `transport`), followed by
the same sync. The first-party adapters stay in the tree until live parity has
held across the nightly check (ADR-0047 §2.3).

**What LiteLLM does and does not change.** It replaces the per-provider wire
code and supplies the capability/pricing metadata; the native tool protocol,
typed model faults, repeat/chain/grounding guards, retries, breaker, fallback
chain and budget guard are harness-level and behave identically on either
route. Known gap under `litellm`: Anthropic `cache_control` breakpoints are
not applied.

### Nightly live check

The smoke set is: greeting, datetime tool → reply, act-don't-announce, and
`live.multistep_sop` — a three-step SOP (schema → transform → write) over
in-memory tools sized past the stale-observation cap, asserting every step ran
once, at most 8 ticks, and no `repeat` guard (the issue #203 failure shape).
Ollama Cloud runs it too (`ollama_chat/glm-5.3:cloud` through the LiteLLM
adapter, `OLLAMA_API_KEY` **and `OLLAMA_API_BASE=https://ollama.com`** — without
the base LiteLLM talks to `localhost:11434`, which on a developer box is the
local daemon forwarding `:cloud` models and on a runner is nothing; smoke only —
the adapter has no first-party conformance column).

`.github/workflows/live-providers.yaml` (03:00 UTC, or manual dispatch) runs per
provider whose key secret is set: record conformance fixtures from the real
endpoint → replay the suite → `scripts/live_smoke.py` on the own wire →
again with `--transport litellm`. Failures open or comment on a `live-check`
issue; recordings are uploaded as run artifacts (commit the ones you want to
keep under `tests/action/model/conformance/fixtures/`). Locally:

```bash
OPENAI_API_KEY=... python scripts/live_smoke.py --provider openai --model gpt-4o-mini
```

### Parity matrix

The conformance suite (below) asserts the same normalised `ModelResponse` from
every adapter for the same logical exchange. Authored wire fixtures; ✓ =
passes, ○ = not applicable to the provider.

Each first-party column is asserted under both transports (`httpx` own wire and
`litellm` delegation); the `litellm` column is the adapter itself.

| Scenario | openai | anthropic | ollama | groq | openrouter | litellm |
|---|---|---|---|---|---|---|
| text | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| tool_call | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| parallel_tool_calls | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| tool_result_roundtrip (provider-shaped) | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| stream_text | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| stream_tool_call (assembly) | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| truncation → `length` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| cached_usage → `cached_read_tokens` | ✓ | ✓ | ○ (no cache) | ✓ | ✓ | ✓ |
| thinking surfaced | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| retry_429 (Retry-After honoured, 2 requests) | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| error_500 raised | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| malformed_body raised | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |

Groq and OpenRouter replay the OpenAI wire (their adapters subclass it); LiteLLM
replays it through its `_acompletion` seam as real `litellm` response objects.
Recording against live endpoints (`JVAGENT_CONFORMANCE_RECORD=1`) replaces the
authored bodies per provider.

### Conformance suite

`tests/action/model/conformance/` drives **every** adapter through the same
twelve logical exchanges — plain text, one tool call, parallel tool calls, a
tool-result round-trip (asserting the result went back in the provider's own
shape), streamed text, streamed tool-call assembly, truncation, cached usage,
thinking, a 429 retry, a 5xx failure and a malformed body — and asserts the same
normalised `ModelResponse` for each. Responses replay from fixtures: a recording
under `fixtures/<provider>/<scenario>.json` when present, otherwise the authored
wire body in `authored.py`. To (re-)record a provider against its real endpoint:

```bash
JVAGENT_CONFORMANCE_RECORD=1 OPENAI_API_KEY=... pytest tests/action/model/conformance -k openai
```

Recording is skipped (not failed) for providers whose key is absent. Adding a
provider means adding it to `PROVIDERS` and supplying bodies for every
scenario — `test_scenario_matrix_is_complete` refuses a partial matrix.

## Retries and resilience

Every model action retries transient failures (timeouts, transport errors,
408/425/429/5xx, Retry-After honoured) with exponential backoff:
`max_retries`, `retry_initial_delay`, `retry_max_delay`, `retry_backoff_multiplier`,
`retry_jitter`, `retry_on_status_codes`. Two knobs bound the damage (ADR-0046):
`retry_total_deadline_seconds` (default 60 — a retry that would start past the
deadline is not attempted) and `retry_on_timeout` (default on; a completion is
not idempotent at the provider, so operators on tight cost policies can stop
timeouts from being retried). SDK-style exceptions carrying a `status_code`
(LiteLLM) retry like httpx status errors.

Above the adapter, the Orchestrator owns the policy — fallback chain, circuit
breaker, cost ceilings, structured decisions — see
[ORCHESTRATOR.md § Resilience](ORCHESTRATOR.md#resilience-adr-0046).

## Loop integration (Orchestrator)

The Orchestrator think-act-observe loop passes model kwargs to the active
`LanguageModelAction` via `_run_model`.

Generic fields exposed on the loop config:

- `reasoning_effort` (`minimal|low|medium|high`)
- `reasoning_budget_tokens` (budgeted thinking profile)
- `reasoning_enabled` (explicit on/off hint)
- `reasoning_extra` (provider-native escape hatch)
- `mirror_assistant_stream_as_thoughts` (provider decision when unset)

Provider adapter methods on `LanguageModelAction`:

- `translate_reasoning_config(cfg)` -> provider-native kwargs
- `prepare_messages_for_reasoning(messages)` -> optional provider message shaping
- `should_mirror_assistant_stream_as_thoughts(cfg, **kwargs)` -> mirror policy

Current provider translations:

- **OpenAI**: maps to top-level `reasoning_effort` for reasoning models.
- **Anthropic**: maps to `thinking={type:"enabled",budget_tokens:N}` and ensures
  `max_tokens >= N + 1`.
- **OpenRouter**: maps to nested `reasoning` object.
- **Ollama**: maps `reasoning_enabled=True` to `think=true`.

For final/forced review passes, profile `"final"` is used so providers can strip
reasoning/thinking options automatically.
