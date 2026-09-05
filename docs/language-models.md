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
harness owns retries (`num_retries=0` is sent; `BaseModelAction` retries on the
exception's `status_code`), streaming is assembled with
`litellm.stream_chunk_builder`, and `provider: litellm` works in slot overrides.
Reference the class as `LiteLLMLanguageModelAction` in `model_action_type`.

### Parity matrix

The conformance suite (below) asserts the same normalised `ModelResponse` from
every adapter for the same logical exchange. Authored wire fixtures; ✓ =
passes, ○ = not applicable to the provider.

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
