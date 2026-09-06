# Runbook — Canary the LiteLLM transport on one deployment

> **When to use:** you want deployed agents to move onto the LiteLLM route
> (ADR-0047) without betting the fleet on it. One deployment runs on LiteLLM,
> the rest stay on the first-party wire, and you compare before rolling
> forward.
>
> **What changes:** only *how* the model is called. Same `agent.yaml`, same
> model ids, same keys, same graph — the transport is an environment switch
> read at call time, so there is no graph sync and rollback is a restart.
>
> **Related:** [ADR-0047](../adr/0047-transport-delegation-and-adapter-fold.md),
> [`docs/language-models.md`](../../docs/language-models.md) ("Transport
> switch", "Migrating a deployed agent to LiteLLM"),
> [`docs/environment-keys-reference.md`](../../docs/environment-keys-reference.md)
> (`JVAGENT_MODEL_TRANSPORT`), the decision to keep `httpx` as the code default.

---

## 0. Prerequisites

- The image installs the pinned extra: `pip install "jvagent[litellm]"` (or
  `requirements-all.txt`, which carries the same single-minor pin). Without it
  the server boots and the first model call raises a clear error — not a
  canary you want.
- The canary and the control run the **same jvagent version and the same
  `agent.yaml`**. Otherwise you are measuring two things.
- You can read interaction telemetry: `Interaction.observability_metrics`
  (`model_call`, `orchestrator_activation` events) or the logs DB
  ([`docs/logging.md`](../../docs/logging.md)).

## 1. Pick the canary

One of:

- **One replica** behind the balancer (ECS/Fargate task, k8s pod, Lambda
  alias) with the env var set on that replica only.
- **One environment** (staging, or a low-traffic production agent).
- **One agent** in a multi-agent app — not possible by env var (the switch is
  process-wide); use the per-action attribute instead:
  `transport: litellm` on that agent's model action, then
  `jvagent <app> --update --source --yes` (merge mode keeps the old value).

Prefer a canary that sees the same traffic mix as the control (same channel,
same skills). Time-of-day and channel skew dominate the metrics below.

## 2. Switch it

```bash
JVAGENT_MODEL_TRANSPORT=litellm      # on the canary process only
```

Restart the process. Nothing else: the first-party action keeps its class,
model, key and endpoint and hands them to the LiteLLM adapter per call
(`litellm_model_id()` / `litellm_call_config()`), then relabels the result as
itself so cost events and slot resolution read as before.

**Confirm it took.** In the canary's `model_call` telemetry events the
`transport` field reads `litellm` (it reads `httpx` on the control; `provider`
stays `openai`/`anthropic`/… on both — the relabelling is deliberate). The
process log also shows `LiteLLM completion()` lines at INFO. If neither
appears, the env var did not reach the process.

## 3. Compare (a few days, same window on both)

| Signal | Where | Expect | Act if |
|---|---|---|---|
| Turn cost | `orchestrator_activation.turn_cost_usd`, `Interaction.usage.estimated_cost_usd` | equal within noise (same model, same tokens) | canary higher → check `drop_params` dropped a cache/`max_tokens` parameter; Anthropic prompt caching is a known gap under litellm |
| Model-call latency | `model_call.duration` | equal ± a few hundred ms | consistent +1s → LiteLLM routing/retry overhead; compare `finish_reason` |
| Tick shape | `orchestrator_activation.tick_count`, `tools_invoked`, `ended_via` | identical distribution | more `(guard)` / `model_error` / `model_truncated` → protocol or tool-call parsing difference; capture a transcript and file it |
| Failures | `ended_via` in (`model_error`, `budget_exhausted`), breaker log lines (`circuit open`), 5xx at `/interact` | none new | any → roll back, keep the log |
| Prompt cache hits | provider dashboard (not yet in telemetry) | equal | canary lower on Anthropic → the known `cache_control` gap; stay on httpx there |
| Cold start (serverless) | first-request latency after deploy | +~2s on the canary | over budget → keep httpx on Lambda, or pre-warm |

Read a handful of canary transcripts by hand as well. The numbers above miss a
reply that is merely worse.

## 4. Roll forward or back

**Forward**, in this order, each step its own deploy:

1. Set `JVAGENT_MODEL_TRANSPORT=litellm` fleet-wide (still reversible by env).
2. Later, if you want the adapter proper: add `jvagent/litellm_lm`, point the
   orchestrator slots at `LiteLLMLanguageModelAction` with LiteLLM ids
   (`openai/gpt-4.1`), sync in **source mode**, verify `provider: litellm` in
   telemetry. Steps 3–4 of the migration guide.

**Back**: unset the env var (or set `httpx`), restart. No data changes; no
sync. If you took step 2 above, revert the YAML and sync in source mode again.

## 5. Bumping the pin

litellm is pinned to one minor (`>=X.Y.Z,<X.Y+1`) in four places kept identical
by `tests/test_requirements_sync.py`. To move:

```bash
.venv/bin/python -m pip install "litellm==<new>"
.venv/bin/python -m pytest tests/action/model tests/action/orchestrator/test_native_tool_protocol.py
OPENAI_API_KEY=... python scripts/live_smoke.py --provider litellm --model openai/gpt-4.1
OPENAI_API_KEY=... python scripts/live_smoke.py --provider openai --model gpt-4.1-mini --transport litellm
```

then update the spec in `pyproject.toml` (both the `litellm` and `test`
extras), `requirements-all.txt`, and the two action `info.yaml` files, and run
the canary above on the new version before the fleet. Dependabot proposes the
bump weekly; it does not run the live smoke.
