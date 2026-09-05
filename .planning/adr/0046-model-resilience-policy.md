# ADR 0046 — Harness-owned model resilience policy

**Status**: Accepted
**Date**: 2026-09-05
**Relation**: Phase 3 of [`.planning/specs/2026-09-05-model-integration-remediation.md`](../specs/2026-09-05-model-integration-remediation.md). Builds on the contract (Phase 1), the capability registry and metadata pricing ([ADR-0045](0045-capability-driven-model-integration.md)) and the typed model faults of [ADR-0044](0044-native-tool-calling-protocol.md). Realises the cost policy of [ADR-0041](0041-gearing-and-cost-policy-in-core.md) with real ceilings. Closes the 2026-09-01 review MEDIUMs on retry deadline and non-idempotent retries.

---

## 1. Context

After ADR-0044 a provider outage is reported honestly (`model_error` →
`model_unavailable_text`), but the turn still fails, the same dead provider is
retried on every turn, a retry storm can run ~15 minutes under Retry-After, a
retried completion can bill twice, and nothing caps what a conversation spends.
The JSON-text protocol (still used for models without tool calling) relies on
prompt obedience for its decision shape even on providers that can validate a
schema.

None of this is a provider concern. It is **policy the harness owns**, expressed
in `agent.yaml`, and it must work identically over the first-party adapters and
the LiteLLM adapter.

## 2. Decision

### 2.1 Fallback chain (per slot, same tick)

`model_fallbacks` / `light_model_fallbacks` on the Orchestrator: an ordered list
of `{model, model_action_type?}` (or bare model ids on the primary's action).
When the primary call fails **after the model layer's own retries**, the next
candidate is tried within the same tick with the same request — model id
swapped, the primary's reasoning passthrough dropped, the candidate's own
capability gates applied (parallel calls, output ceiling). The loop sees only a
success or a final `model_error`. Fallbacks used are recorded on the activation
event (`fallbacks_used`) and logged.

### 2.2 Circuit breaker (per action × model, per event loop)

`jvagent/action/model/resilience.py:CircuitBreaker`: `circuit_breaker_failures`
consecutive failures open a circuit for `circuit_breaker_cooldown_seconds`; an
open circuit is skipped by the chain; after the cooldown one probe is allowed
(half-open), whose outcome re-closes or re-opens it. State is per event loop
(serverless warm starts start closed). `OrchestratorInteractAction.healthcheck()`
reports the current-loop circuits. `0` failures disables the breaker.

### 2.3 Retry hygiene (model layer)

- `retry_total_deadline_seconds` (default 60): a retry that would start past
  the deadline is not attempted; the last error propagates. `0` restores the
  unbounded behaviour.
- `retry_on_timeout` (default `True`): completions are not idempotent at the
  provider, so an operator on a tight cost policy can stop timeouts from being
  retried; transport errors and 429/5xx are unaffected. No provider offers an
  idempotency key for completions, so the review's "idempotency key where
  supported" reduces to this switch.

### 2.4 Budget guard

- `max_turn_cost_usd`: before each tick the turn's `model_call` events are
  priced (`estimate_cost`, now metadata-backed); at or over the ceiling the
  loop ends with `ended_via=budget_exhausted` and the existing single
  partial-compose delivers what was gathered.
- `max_conversation_cost_usd`: each turn's cost is folded into
  `conversation.context["_cost_usd_total"]` (only when a ceiling is set); a
  turn that starts over it makes no model call and replies with
  `budget_exhausted_text` (`ended_via=conversation_budget`).
- `turn_cost_usd` is recorded on every activation event.

### 2.5 Structured decisions (JSON protocol)

When `structured_decisions` is on and the model's capabilities say
`supports_structured_output`, the decision schema (`constants.DECISION_SCHEMA`)
travels as `response_format: json_schema` (OpenAI-family, LiteLLM) or as a
forced `orchestrator_decision` tool on Anthropic (which has no response_format);
the decision is read from the tool call. Otherwise JSON mode as before.

## 3. Consequences

- A provider outage degrades to the next configured model, not to a failed
  turn; a dead provider costs one probe per cooldown instead of one failure
  per turn.
- Worst-case latency of a failing call is bounded (60 s default) instead of
  minutes.
- Cost ceilings are real numbers because pricing is (ADR-0045); an agent
  without ceilings pays no extra write.
- The JSON protocol's decision shape is provider-validated wherever the
  provider can; weak models on capable providers stop producing "not valid
  JSON" nudges.
- The breaker is process-local. Multi-worker deployments trip independently;
  a shared breaker (Redis) is a follow-up if needed.

## 4. Alternatives considered

- **LiteLLM Router for fallbacks/cooldowns.** Rejected for now: it would apply
  only to the LiteLLM adapter, and the policy must be uniform across adapters
  and visible in jvagent's own telemetry. Revisit in Phase 4 if the first-party
  adapters fold.
- **Hard-fail the turn on the turn ceiling.** Rejected: the partial-compose is
  one bounded call that turns spent work into an answer.
