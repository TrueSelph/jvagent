# Language model actions (HTTP retries)

`BaseModelAction` (including all `LanguageModelAction` providers: OpenAI, Ollama, Anthropic, OpenRouter) supports automatic retries for **transient** HTTP failures so a single timeout or connection blip does not abort long-running flows (e.g. `CockpitInteractAction` think-act-observe loops).

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

1. **`reasoning_effort`** on `jvagent/openai_lm` or on `jvagent/cockpit_interact_action` (recommended for OpenAI).
2. Legacy nested form **`reasoning: { effort: medium }`** on the cockpit action — it is translated to top-level `reasoning_effort` for native OpenAI only.

**OpenRouter** (`OpenRouterLanguageModelAction`) does **not** apply this reshaping: it keeps the nested `reasoning: { effort: ... }` body OpenRouter expects. Use the `reasoning` dict there as before.

Example (`agent.yaml`):

```yaml
- action: jvagent/openai_lm
  context:
    model: o3-mini
    timeout: 180
    reasoning_effort: medium
- action: jvagent/cockpit_interact_action
  context:
    model_action_type: OpenAILanguageModelAction
    model: gpt-5.1-2025-11-13
    reasoning_effort: low
```

## Loop integration (CockpitInteractAction)

`CockpitInteractAction` passes a provider-agnostic `ReasoningModelConfig` to the
active `LanguageModelAction` and providers translate it to native API kwargs.

Generic fields exposed on `CockpitInteractAction`:

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
