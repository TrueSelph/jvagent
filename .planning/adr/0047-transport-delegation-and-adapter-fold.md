# ADR 0047 — Transport delegation and the evidence-gated adapter fold

**Status**: Accepted
**Date**: 2026-09-05
**Relation**: Phase 4 of [`.planning/specs/2026-09-05-model-integration-remediation.md`](../specs/2026-09-05-model-integration-remediation.md). Builds on the contract (Phase 1), the LiteLLM adapter and capability registry ([ADR-0045](0045-capability-driven-model-integration.md)) and the resilience policy ([ADR-0046](0046-model-resilience-policy.md)).

---

## 1. Context

The remediation plan's Phase 4 said: *with the parity matrix green, fold the
first-party OpenAI/Anthropic adapters into thin shims over LiteLLM and delete
the hand-rolled wire code (~1,500 lines)*. The matrix is green — on **authored**
fixtures replayed offline. No provider has been recorded live, and deleting the
wire code on that evidence alone would be deciding on a slide, which the plan
explicitly rules out.

Worse, the LiteLLM column of that matrix was not actually running on `dev`:
the Phase 2 conftest change that added the `litellm` provider never landed in
PR #179 (the fixture module and docs did). ADR-0045 §2.5 described a gate that
did not exist. This phase restores it and adds a matrix-completeness assertion
so a column cannot vanish silently again — a second reason the fold must wait
for live, recorded evidence rather than an authored matrix.

Two other Phase 4 items turned out different from the plan on inspection:

- PageIndex's vendored core (`jvagent/action/pageindex/core/`) must stay
  identical to upstream (it is excluded from formatters for that reason). Its
  LLM calls already route through the jvagent model action via
  `pageindex/llm_bridge.py` whenever one is set; the direct `litellm` calls are
  the no-model-action fallback and token counting. There is nothing to move
  without patching upstream code.
- The CUCS "live runner" (`jvagent/testing/live_runner.py`) already drives a
  real model; what was missing was an Orchestrator wired to a real provider
  action without a graph, a fixed smoke scenario set, and a schedule.

## 2. Decision

### 2.1 `transport` switch on every language-model action

`LanguageModelAction.transport: "httpx" | "litellm"` (default `httpx`), with
`JVAGENT_MODEL_TRANSPORT` as a process-wide override. Under `litellm` the action
delegates `_query` / `_query_stream` to an in-memory
`LiteLLMLanguageModelAction` configured with **this action's** model
(`litellm_model_id()` → `provider/model`), credentials and endpoint
(`litellm_call_config()`, overridden per provider so the same key and base URL
its own client would use are handed over). The result is relabelled with the
action's provider and model, so observability, cost events, slot resolution and
`agent.yaml` are unchanged. Retries, breaker, fallbacks and budgets sit above
the switch and apply to both transports.

Same class names, same configuration, same credentials: an operator flips one
attribute (or one environment variable across a fleet) to run production traffic
through LiteLLM and back.

### 2.2 Parity is measured, not asserted

- The conformance suite gains a transport axis: every first-party adapter runs
  the twelve scenarios under `httpx` **and** under `litellm` delegation
  (6 → 11 adapter columns).
- A nightly `live-providers` workflow re-records each provider's fixtures from
  its real endpoint, replays the suite on the recordings, and drives the real
  Orchestrator loop through three smoke scenarios (reply; tool call then reply;
  act-don't-announce) on **both** transports (`scripts/live_smoke.py`,
  `jvagent/testing/live_smoke.py`). A provider without a key secret is skipped;
  failures open or comment on a tracking issue — nothing gates merges.

### 2.3 The fold is deferred, with an explicit gate

Deleting the hand-rolled wire code happens in a later release **only when**
the nightly workflow has run the `litellm` transport green across the
first-party providers for a sustained period and at least one production agent
has run on it. Until then both transports ship; `httpx` stays the default.

### 2.4 PageIndex

No change to the vendored core. `llm_bridge` remains the integration point:
with a model action in context, ingestion and search LLM calls flow through
jvagent's observability and cost ledger (and therefore through the transport
switch); the direct `litellm` calls remain only as the no-model-action fallback
and for token counting.

## 3. Consequences

- Operators can A/B LiteLLM against the own-wire clients on real traffic with
  a one-line change and an immediate way back.
- The conformance matrix now proves the delegate produces the same normalised
  response for every first-party provider's scenarios.
- The `litellm` transport does not apply Anthropic `cache_control` breakpoints
  (LiteLLM's cache markers are message-level and not mapped in this phase) —
  prompt-cache economics differ under delegation. Documented; measured by the
  nightly cost telemetry before any fold.
- The nightly workflow spends a few cents per provider per night and needs
  provider keys as repository secrets; without them it is a no-op.

## 4. Alternatives considered

- **Fold now on authored parity.** Rejected: the plan's own rule.
- **LiteLLM mandatory dependency.** Rejected for this phase: the extra stays
  optional; an install without it keeps the own-wire clients.
- **Patch PageIndex core to call the model action.** Rejected: breaks the
  identical-to-upstream invariant; the bridge already achieves the goal.
