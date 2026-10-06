# Pydantic AI integration: critical engineering review

Date: 2026-10-05
Checkout: `codex/pydantic-inspired-skill-pilot`
Committed revision: `82012aef` (review also includes the five pre-existing uncommitted reasoning/tool-event restoration files).
Disposition: **Promising pilot; not qualified as an enterprise harness. Fix the trust boundary and operational defects before expanding its surface.**

## Scope and evidence

Reviewed the complete pilot package: contracts, Action-tool compiler, JV-to-Pydantic model adapter, capability construction, execution limits, evidence collection, and TaskStore adapter. Traced integration through Orchestrator admission/dispatch/egress, legacy rollback, research SKILL.md resolution, provider transports, cost accounting, graph persistence, conversation locks, and the jvchat event/usage contract. Inspected pilot unit, subprocess-restart, effect-fixture, live-model and matched-evaluation tests, the implementation plan, ADR-0056, and accumulated evidence. Existing changes outside this integration are not independently requalified by this review.

Verification performed in the current Python 3.10 virtualenv:

- Full suite: **4,163 passed, 9 skipped, 31 warnings**, exit 0, 135.94 seconds. Command: `.venv/bin/python -m pytest tests/ -q -o addopts=''`. Log: `/tmp/jvagent-pilot-review-pytest.log`.
- Focused pilot/model/continuation/context/legacy baseline slice passed; its three live-provider tests were gated off. The full-suite skips also include optional-dependency/fixture cases.
- Isolated adversarial executions reproduced wrapper-argument authorization spoofing, missing schema enforcement, receipt loss after search truncation, unrestricted conversational output, failed-fetch receipts, evidence saturation, long-URL failure, unvalidated inline citations, and loss of provider finish reason. These used real installed Pydantic AI 2.54.0 adapter code and deterministic local tools, with no external effects or paid calls.
- Read-only browser inspection of the user's running jvchat found the most recent completed exchange displaying a Reasoning disclosure, one tool call, and a cited reply. It also showed earlier incomplete turns and a research report that explicitly could not save a file or ingest knowledge. That confirms the current limited surface and visible event rendering; it is **not** a fresh end-to-end model smoke or recovery qualification. No new browser prompt was submitted during this review.
- No runtime code changed, no services restarted, no commit/push performed. This document is the review deliverable.

Severity: P1 requires correction before broader use; P2 is a material reliability or architectural problem to close before enterprise qualification; P3 is maintainability/documentation debt. Findings marked inherited concern a reused seam, rather than a defect introduced by Pydantic AI itself.

## Prioritized findings

### F01 — P1: model arguments can substitute the tool identity used for authorization

**Confirmed by execution.** `pilot/tools.py:128` defines model-invoked `execute(ctx, _original=original, _tool_name=name, **kwargs)`. These defaults are ordinary keyword parameters. `PydanticTool.from_schema` at `:239` skips JSON-schema argument validation; the supplied custom validator at `:230` checks only authority-key names. `_tool_name` and `_original` are not among those keys. The JV `Tool.call` also forwards kwargs without schema validation (`jvagent/tooling/tool.py:63`).

Reproduction used a schema requiring a string `query` and forbidding additional properties, then submitted `{"query":123,"_tool_name":"web_fetch__fetch"}` to the search binding. The installed Pydantic validator accepted it; authorization checked **fetch**, while the wrapper executed **search**, receiving integer `123`. The host access callback trusts that substituted name (`orchestrator_interact_action.py:1465`). This bypasses per-tool ACLs when the substituted label is allowed. The currently admitted operations are read-only, which limits impact; the pattern must never be carried into write tools.

**Correction:** capture bindings in a factory closure whose callable exposes only `ctx` and model kwargs. Validate the complete argument object against the actual supported JSON Schema before invoking any callback or Action. Bind the resource label and effect metadata exclusively from the server-owned Tool. Reject reserved wrapper keys even when an Action intentionally permits additional properties. Add a full Agent-run adversarial regression, not only a direct wrapper test.

### F02 — P1: failure to resolve access control grants access

**Confirmed by source; inherited security seam.** `orchestrator/access.py:20` catches errors looking up AccessControl and returns `None`; exceptions in `policy_applies()` do the same. `is_tool_allowed` at `:38` treats `None` as unrestricted access. The pilot's supposedly fresh, per-call authorization check uses this helper (`orchestrator_interact_action.py:1465`). A storage/configuration failure discovering the policy therefore differs from a policy evaluation failure: discovery fails open, evaluation fails closed.

**Correction:** distinguish intentionally absent/non-enforcing policy from policy-resolution failure. When a policy is configured or expected, discovery/evaluation errors must deny the operation and emit an actionable security event. Preserve an explicit unrestricted deployment mode for compatibility; do not silently infer it from an exception. Test policy lookup failures as well as `has_action_access` failures.

### F03 — P1: configured dollar-spending guards are bypassed by the pilot — partially remediated

**Confirmed by call-path inspection.** Legacy checks turn spend in `loop.py:265` and conversation spend at `loop.py:430`. Pilot selection in `orchestrator_interact_action.py:1080` bypasses that loop. `pilot/runtime.py:463` passes request/tool/token limits but no cost limit; its adapter at `:331` also omits JV cost metadata. Conversation cost is settled after a successful driver return (`orchestrator_interact_action.py:1087`), which records spending without preventing it. Generic failures rethrow before settlement.

An agent configured with `max_turn_cost_usd` or an already exhausted `max_conversation_cost_usd` can still start and continue pilot model requests. A failed pilot may also leave conversation spend understated, despite Interaction-level metrics being available.

**Correction and current disposition:** Phase 54 adds a pre-request reservation to the capability pilot. Before each JV `ModelRequest` crosses into the configured model Action, it estimates a conservative prompt-size bound including the active tools, applies the bounded output-token setting, uses the configured provider/model pricing without cache discounts, adds 25% headroom, and checks both per-turn and accumulated conversation spend. Unknown pricing blocks before the request. Focused tests exercise the estimate, output clamp, unknown-pricing failure, prior conversation spend, and the guard's position before the provider call.

This reduces but does not eliminate spend overshoot: it is still an application estimate based on bundled/LiteLLM pricing and request serialization, not a provider-enforced reservation. Provider price changes, hidden billing units, or invoice adjustments can exceed it. A transport failure with no usage remains explicitly unknown and blocks a subsequent budgeted request; it cannot be reconciled automatically. Legacy-driver behavior and provider billing reconciliation remain separate qualification work.

**Streaming accounting correction (Phase 58):** lazy provider errors during streamed response consumption are now recorded as failed, unreported-cost attempts. Before this correction, stream construction was marked successful even when the first read returned an HTTP error, allowing the browser usage summary and pilot settlement to understate uncertainty. A real browser request against the disposable app verified the failed attempt now increments the visible unknown-cost count. Provider billing reconciliation, successful usage/cost receipts, and hard provider-side ceilings remain unqualified.

### F04 — P1: factual-output grounding contract — substantially remediated; semantic support remains open

**Original findings are closed at the contract/rendering boundary.** The capability-pilot Agent now accepts only `ResearchBrief`; `ConversationalReply` remains readable for legacy snapshots but is rejected by fresh research output validation (`pilot/runtime.py:341`, `:373`, `:941`, `:1003`). Each finding is a typed claim with host-known source IDs, a designated supporting fetched source, a quote anchor found in its bounded excerpt, and fresh host-observed timestamps. The renderer generates citations only from validated evidence; model-written URLs, Markdown links, and recognizable inline source-number citations are rejected. TaskStore independently revalidates the persisted output before delivery/completion.

**Phase 60 closes a remaining citation-marker variant:** plain-text `Source 99`, `[Source 99]`, `[99]`, and `Source #99` markers are now rejected from both claims and limitations, preventing the model from placing a fake reference immediately before host-rendered citations. Adversarial schema tests cover those forms.

**Semantic entailment is still not established by structural validation.** A quote can occur in a fetched excerpt without supporting the associated claim; host-observed freshness is not publication-date freshness. Continue adversarial evidence-support/calibration evaluations and qualify a separately bounded verifier for workloads that require factual entailment. Do not describe a valid quote anchor as proof that a claim is true.

### F05 — P1: truncating search JSON destroys the evidence used to validate the answer

**Confirmed by execution.** The wrapper clips tool output before calling the evidence observer (`pilot/tools.py:214`). The collector then JSON-decodes the clipped string (`pilot/runtime.py:94`). A legitimate 8,263-character result containing five source objects became a 4,000-character invalid JSON fragment and produced **zero receipts**, silently. The model still sees partial useful links, can cite them, and is then rejected after completing its run.

**Correction:** extract and persist structured source receipts from the full bounded Action result before reducing model-visible content. Clip source items/snippets while preserving valid JSON, rather than slicing serialized JSON. Keep provenance extraction and model-context limits distinct. Test results just below/above the threshold, long snippets, Unicode and multiple source objects.

### F06 — P1: refused, failed, and non-successful fetches become successful source receipts

**Confirmed by execution and production Action contract.** The collector adds a fetch receipt whenever the requested URL is valid (`pilot/runtime.py:133`), without checking retrieval success. WebFetch returns refusals and unsupported-content messages as ordinary strings (`web_fetch_action.py:145`, `:250`) and renders non-200 HTTP responses with `# HTTP ...` (`:263`). JV Tool wraps ordinary strings as successful ToolResults (`tooling/tool.py:63`). The wrapper's `is_error` check therefore does not protect this seam.

Both a `(refused: ...)` body and a 404 payload became accepted receipts. Redirected fetches also retain the requested URL instead of extracting the final source URL from the Action's result. A source may appear verified despite no usable page having been retrieved.

**Correction:** introduce a minimal typed read-result envelope carrying success/refusal/error, requested/final URL, content type and bounded text. Adapt the existing Action without weakening its SSRF checks. Search snippets and successful page retrieval must have different provenance states; a refused fetch may retain a separately observed search snippet but must not promote it into a fetched-page receipt.

### F07 — P2: completed evidence accumulates until new research cannot admit new sources

**Confirmed by execution.** Every new run seeds evidence from the latest completed run (`orchestrator_interact_action.py:1429`). Collector admission stops at 30 references (`pilot/runtime.py:61`, `:129`, `:142`), with no eviction or task-relevance policy. With 30 inherited sources, a new valid source was silently ignored. Subsequent unrelated research can fail citation validation indefinitely for that session/configuration.

Evidence has no fetched-at time, HTTP outcome or per-run origin (`pilot/contracts.py:44`), so old receipts also remain eligible for current-fact answers.

**Correction:** bound evidence per objective/run, retain an explicit reference to earlier evidence for genuine follow-ups, and prioritize current-run receipts. Store timestamps and provenance; make stale-source reuse visible and require fresh retrieval for workloads that need current facts. Add a 31st-source, topic-change, stale-follow-up regression.

### F08 — P2: valid long URLs abort evidence collection

**Confirmed by execution.** Source IDs are limited to 256 characters while URL fields permit 2,048 (`pilot/contracts.py:47`). Search uses the URL as the source ID when no ID exists (`pilot/runtime.py:119`); fetch always uses the requested URL (`:135`). A valid roughly 320-character URL raised `ValidationError` instead of becoming evidence. This exception happens after the Action has completed and can terminate the entire run.

**Correction:** generate bounded stable IDs from normalized URLs, retaining the full URL separately; preserve any Action-provided stable ID after validating its bounds. Test signed/query-heavy URLs, redirects and Unicode.

### F09 — P2: provider termination and request controls are lost in the adapter

**Confirmed by execution/source.** A JV response with nonempty text and `finish_reason='length'` became a Pydantic response with `finish_reason=None` (`pilot/runtime.py:323`, `:331`). Only an entirely empty response carries the finish reason into the error handler. `content_filter` with partial text has the same semantic-loss risk.

The bridge forwards only model/tools/max_tokens/temperature/top_p (`:296`). It omits tool-choice controls, parallel-call policy, response format and reasoning settings exposed by JV's `ModelRequest` (`action/model/contract.py:236`). The host resolves `_reasoning` but discards it (`orchestrator_interact_action.py:1346`). Provider reasoning/replay IDs are not retained. Existing request knobs therefore are not generally portable to this driver.

**Correction:** use an explicit production model adapter with a declared supported request/response contract. Map finish reasons and provider metadata; fail clearly on unsupported settings. Preserve raw tool-argument parse failures instead of converting malformed JSON into an empty mapping (`action/model/contract.py:113`). Qualify OpenAI and Ollama Cloud separately, including reasoning-heavy truncation, filtering, invalid tool arguments and multi-call responses.

### F10 — P2: user requests and proactive context were silently clipped — partially remediated

**Original finding confirmed; current pilot request path no longer clips an accepted question or proactive context.** The capability pilot rejects questions above 20,000 characters and preserves the complete accepted question in its snapshot and retry identity (`orchestrator_interact_action.py:1520`, `pilot/contracts.py:317`, `pilot/state.py:169`). Over-limit user-request and proactive-context regressions verify an explicit reply before task/model dispatch (`tests/action/orchestrator/pilot/test_orchestrator_pilot.py:477`). A retry regression proves two requests that share the old 2,000-character prefix but differ after it no longer alias (`tests/action/orchestrator/pilot/test_pilot_state.py`). This correction is scoped to the capability pilot; do not infer a general input-size contract for every legacy driver.

**Correction:** validate request size at admission and return an explicit error or use a bounded context strategy with an unabridged objective reference. Retry identity should use the complete accepted request or its digest. Test differences after the boundary.

### F11 — P2: retries reload evidence, not the execution state or original budget

**Confirmed by source; intentional pilot limitation, enterprise blocker.** Snapshot schema v3 contains question/evidence/output but no model messages, active capability state, tool-attempt ledger, accumulated run usage or remaining budget (`pilot/contracts.py:96`). `parked_retry` considers parked tasks only (`pilot/state.py:135`); a worker killed while running leaves an active record unless another path explicitly parks it. The next pilot turn can create another task. Resuming a parked task constructs a fresh Agent with full new limits (`orchestrator_interact_action.py:1457`, `:1536`).

Current subprocess tests prove snapshot readability; write/receipt crash tests exercise a **test-only** effect fixture. They do not prove production Pydantic loop continuation, model-call replay accounting or bounded lifetime spend across retries. Restarting a read-only objective is useful, but is not durable execution.

**Correction:** define restart-versus-resume semantics explicitly. Persist a minimal checkpoint/message/usage contract in the existing graph, with invocation states and a lifetime budget. Reconcile abandoned active runs through a lease-aware recovery path. Avoid a second workflow database; qualify graph-backed durability before enabling writes or subagents.

### F12 — P2: final response publication and task completion have a crash window

**Confirmed by ordering; architectural gap.** The host publishes the answer before completing the task (`orchestrator_interact_action.py:1577`, `:1584`). A crash or persistence failure between those steps leaves a user-visible success without a completed pilot task. The generic exception path may then mark the task failed even though the answer was sent (`:1683`). The snapshot completion check accepts a caller-supplied `delivered=True`; it does not atomically bind publication acknowledgment to a persisted output ID (`pilot/state.py:219`).

**Correction:** persist the validated final output and a stable delivery ID before publication, then acknowledge egress idempotently using the existing bus/outbox boundary. Qualify crashes before publication, after acceptance and before terminal state, and after terminal state. Distinguish bus acceptance from remote-client receipt; do not promise exactly-once external delivery without a channel contract.

### F13 — P2: cancellation leaves the surrounding TurnRun lifecycle inconsistent

**Confirmed by source.** Pilot cancellation persists a cancelled task and rethrows `asyncio.CancelledError` (`orchestrator_interact_action.py:1585`). The enclosing execution wrapper catches `Exception` only (`:1043`); cancellation skips `complete_turn` and `fail_turn`, then reaches runtime persistence in `finally` (`:1049`). The pilot task may be cancelled while its outer journal remains nonterminal. Cancellation-time persistence can also be interrupted, and a tool-call event has no paired result because the tool wrapper catches `Exception` only (`pilot/tools.py:184`).

**Correction:** establish one terminal cancellation transition shared by task, outer run and streaming state. Use bounded cleanup appropriate to the storage backend, and finish tool events with explicit cancellation/timeout status. Test cancellation at model wait, Action wait, evidence checkpoint and final delivery.

### F14 — P2: recovered tool errors were not a coherent model/UI protocol — partially remediated

**Original mismatch remediated at the pilot boundary.** `PilotToolOutcome` carries an explicit `failed`, `timed_out`, or `cancelled` status independent of its text; the Orchestrator renderer consumes that status to set `is_error`. Declared read failures return a bounded recoverable observation to the research skill, and cancellation emits a paired terminal tool event (`pilot/tools.py:324`, `:365`; `orchestrator_interact_action.py:4220`; `tests/action/orchestrator/pilot/test_pilot_tools.py:499`, `:610`). Observer failures and cancellation during persisted delivery still require lifecycle qualification.

**Correction:** classify configuration/security failures as terminal, ordinary read failures as typed observations or Pydantic `ToolFailed`/bounded retry outcomes, and return explicit event status independent of string prefixes. The skill should decide how to proceed from an unavailable page, without core domain heuristics.

### F15 — P2: live model streaming lifecycle and progress remain only partially qualified

**Original finding confirmed; model streaming path now implemented, but production behavior remains partially qualified.** The pilot detects JV `query_messages(stream=True)` and forwards response and reasoning deltas through Pydantic AI's stream hook; complete-only providers retain the compatibility fallback. Draft text stays internal until the structured output validates. Synthetic integration tests verify streamed reasoning and output-tool reconstruction. Cancellation coverage verifies provider-generator closure; retry and empty-stream regressions verify failed/success terminal attempt events. Real browser calls reached Ollama Cloud but returned HTTP 401, so successful provider-backed browser streaming and reasoning display remain unqualified. Browser smoke exposed and fixed late stream-failure accounting: the UI now reports the provider-unreported attempt as unknown cost.

The current source maps the channel-effective Orchestrator `tool_call_timeout` and bounded `max_concurrent_tools` into Pydantic AI (`orchestrator_interact_action.py:2035`, `pilot/runtime.py:934`). Runtime-construction tests verify these values and reject invalid bounds (`tests/action/orchestrator/pilot/test_pilot_runtime.py:656`, `:694`, `:728`), so the prior 45-second/single-call mismatch is closed. Transport heartbeat/progress, reconnect semantics, idle proxy timeout behavior, and successful provider-backed browser streaming still need qualification.

**Correction:** provide cancellation-aware model event streaming and transport heartbeat/progress semantics while withholding an unvalidated final output. Use one typed run-policy mapping for timeouts, concurrency and limits, explicitly rejecting unsupported overrides. Test slow reasoning-only responses, client disconnect/reconnect, cancelled tools and idle proxy timeout behavior.

### F16 — P2: host context required caller binding and replay protection — remediated

**Original finding confirmed; current source closes the identified trust gap.** `orchestrator/host_context.py` now requires a dedicated `JVAGENT_HOST_CONTEXT_SECRET`, verifies issuer, audience, version, bounded expiry, and exact agent/user/session values resolved by the server. The host-supplied `run_id` is intentionally only a signed request label; it is explicitly not authority and is not presented as the server's correlation ID. The nonce is durably recorded in the caller's Conversation while holding its mutation lock, together with the server-generated correlation ID. A repeated nonce, unavailable Conversation, malformed ledger, persistence error, or full bounded ledger rejects promotion. The InteractWalker consumes the envelope only after resolving the live caller and creating its correlation ID. Verified context is therefore bound to the authenticated session and a one-use nonce, not merely to a caller-selected run label.

The review's original statements that the context lacked expiry, caller binding, replay protection, and key separation are stale for the current tree. Focused tests cover caller/expiry binding, dedicated-key use, tampering, one-use behavior, fail-closed ledger errors/capacity, and concurrent replay. The remaining assurance boundary is deployment qualification: the browser smoke in Phase 53 and current tests do not prove atomic nonce consumption across production multi-worker/database configurations. Keep this host-only contract generic and independent of Integral or any other application.

### F17 — P2: reported zero costs are overwritten and provenance is too provider-specific

**Confirmed by source.** LiteLLM preserves response cost only if `> 0` (`litellm_lm.py:272`) and its tracking code estimates when `cost <= 0` (`:298`). Ollama tracking does the same (`ollama.py:250`). A legitimate reported zero-dollar call is replaced by a nonzero estimate for a priced model. Meanwhile Interaction labels only `litellm_response_cost` as reported; other non-LiteLLM reported cost is accumulated into `estimated_cost_usd` (`memory/interaction.py:625`). Non-finite values are not consistently rejected.

**Correction:** distinguish missing cost from zero, validate finite nonnegative amounts, and represent amount/currency/source/estimated/pricing-version in one provider-neutral cost record. Keep provider-reported token usage distinct from estimated price. Add zero-cost, absent-cost, unknown-model, NaN/Infinity and non-LiteLLM reported-cost tests. The current work improves visibility, but is not invoice-grade cost reconciliation.

### F18 — P3: budget and qualification documentation is stale

**Partially remediated.** The proposed ADR-0056 is retained as historical planning evidence; its initial ceilings are superseded by current Orchestrator defaults of 32 requests/48 tool calls/100k total tokens/20k output tokens/300 seconds. The pilot now selects one enabled JV skill through exact-name `pilot_skill` (default `research`) and rejects a selected skill unless it declares `output-contract: evidence_required` and satisfies the existing research-evidence contract. Alternate skill selection and proactive TaskStore dispatch for the configured skill are covered by Orchestrator tests. Runtime instructions now refer to the selected JV skill rather than naming `research` unconditionally. The current compatibility matrix documents that narrow selector and its remaining constraints. The older pilot line-count estimate is historical, not a current size measure.

**Remaining correction:** keep the compatibility/qualification matrix tied to tested revisions and update it as support changes. Do not edit accepted ADR history; publish amendments through a new decision record if the architecture decision itself changes. Retain browser/provider outcomes as qualification evidence rather than treating configuration or unit tests as deployment proof.

## Architecture assessment and coverage inventory

| Boundary reviewed | Strength to preserve | Gap / disposition |
| --- | --- | --- |
| Typed contracts and output | Forbid-extra models, bounded run inputs, stable schema version | F04/F08/F10; frozen models do not by themselves validate semantic truth or every nested string length |
| SKILL.md compiler and capability activation | Existing SOP authoring, stable digest, deferred loading, explicit unsupported-feature rejection | Pilot selects one exact-name enabled skill, but admission remains research/evidence-contract specific and permits additional skill-declared read-only Action tools through explicit `effect_class="read"`. General task flows and arbitrary output contracts remain outside the pilot (`orchestrator_interact_action.py:1569`, `:1588`, `pilot/tools.py:174`). |
| Action tool composition | Existing implementations, required-owner checks, per-call access callback, optional effect boundary | F01/F02/F05/F06/F14; never expand writes using the present wrapper |
| JV model Action bridge | Preserves existing credentials/provider transport and avoids another provider configuration registry | F09/F15; production contract must be explicit and feature-tested rather than relying on FunctionModel's generic profile |
| Run policy and resource usage | Real request/tool/token/wall-clock bounds | F03/F11/F15; budgets are per fresh Agent run, not durable objective budgets |
| Evidence and answer rendering | Only declared read tools enter the pilot; observed-reference validation is a useful foundation | F04–F08; receipt existence is not factual support, successful retrieval, or freshness |
| TaskStore persistence | One object-spatial state owner; task+snapshot lifecycle transitions; rollback preservation | F11/F12/F13; state adapter is not a durable execution engine |
| Native harness admission/journal | Existing caller and turn admission are retained (`orchestrator_interact_action.py:983`) | Default HarnessStore is process-local (`harness/runtime.py:108`); deployment must qualify shared leases/journals and recovery semantics |
| Graph concurrency | InteractWalker holds a conversation mutation lock across traversal (`interact_walker.py:573`) | Useful protection; Redis/Dynamo backends are optional and fallback is process-local (`distributed_conversation_lock.py:1`). Lease-renewal failures currently log and continue (`:111`); fencing/lost-lease stop behavior needs multi-worker tests |
| ReplyAction/ResponseBus | Existing publication surface and delivery latch are reused | F12/F14/F15; transient reasoning/tool events are not a durable audit record (`orchestrator_interact_action.py:3653`) |
| Metrics and monetary costs | Provider usage reaches JV observability; estimates have a source marker | F03/F17; no objective-level cost reconciliation or complete attempt ledger |
| Legacy compatibility | Default remains legacy, optional dependency imports are guarded, switching to legacy parks pilot-owned work (`continuation.py:501`) | Good rollback foundation; dual drivers are temporary complexity, not simplification delivered |
| Proactive work | Uses claimed TaskStore objective rather than arbitrary visitor data (`orchestrator_interact_action.py:1214`) | Research-only admission, clipping and recovery limits still apply; no production write qualification |
| Tests and evaluations | Broad passing suite; useful restart and crash-fixture work; honest matched-smoke limitations | Adversarial cases above are absent. Three cases, one replicate each in matched smoke cannot establish failure rates or enterprise reliability |
| Browser interface | Reasoning/tool-call presentation works for the latest visible exchange | Fresh negative-path, recovery, slow-call, reconnect and multi-provider browser qualification still needed |

### Capabilities that are possible but not implemented or qualified here

1. **Subagents/delegation:** no delegated-agent runtime is installed by this pilot. Native Pydantic patterns can support it, but child caller permissions, parent/child tasks, budget sharing, cancellation propagation, depth/concurrency limits and receipt ownership must be defined before admission. Do not equate dependency availability with feature completion.
2. **Production write tools:** effect_invoker is an adapter seam; the production research entry does not pass one. Approval/idempotency/reconciliation proofs in the current crash tests are test-fixture proofs, not production connectors or an approval journey.
3. **MCP and visitor-bound Actions:** explicitly excluded during tool collection (`orchestrator_interact_action.py:1276`). Actions remain a strong authoring boundary, but full runtime compatibility is deferred.
4. **Stateful skills/interviews:** locks, prerequisites, inheritance, hooks, bundled scripts and parameters are rejected by `pilot/runtime.py:358`. This fail-closed design is appropriate; enterprise rollout must publish these differences rather than imply all current skills are supported.
5. **Long-running autonomous jobs:** a maximum 600-second run remains an interactive bounded job. No durable background Pydantic executor, scheduler continuation, child-run budget or staged checkpoints are supplied by this integration.
6. **Provider matrix:** current live evidence emphasizes GLM-5.3 Cloud. OpenAI reasoning, strict tool schemas, refusal behavior, native structured output and provider-specific replay need equivalent qualification. Transport availability is not demonstrated behavioral equivalence.
7. **Operational qualification:** no new two-worker/postgres lost-lease test, load/soak run, p95/p99 latency/error-rate study, or browser disconnect/restart matrix was conducted in this review. Source tests are not those gates.
8. **Minimal bloat:** two engines plus a 1,186-line pilot package and roughly 600-line Orchestrator pilot integration presently add maintenance responsibilities. The plan explicitly intends a later expand/revise/remove decision; retirement has not happened.

## Recommended engineering path

Keep the pilot opt-in and read-only while closing F01–F06. These corrections strengthen the existing boundaries; they do not require replacing Actions, SKILL.md or object-spatial persistence.

Then establish five small, explicit contracts: SkillCompiler, guarded Action binding, JV Model adapter, graph RunCheckpoint, and Egress acknowledgment. Make the Orchestrator select a driver and delegate through those contracts; move research evidence policy out of its large execution method. Keep one source of caller authority, one graph persistence owner, one cost policy and one publication protocol. Avoid adding core intent classifiers, domain-specific prep logic, or a second workflow store.

Before adding writes or delegated agents, close recovery/cancellation/delivery/budget gaps and run the real browser failure matrix. Add contract-level adversarial tests for every finding, then real configured-provider browser scenarios: greeting, long research, failed/refused fetch, large search output, long URL, follow-up beyond 30 sources, spending limit, response truncation, slow model, cancellation, disconnect, backend restart and replay. Record exact revision, source result state, visible events, persisted terminal state and actual/estimated usage separately. Add multi-worker storage/lease testing as its own gate.

Finally compare a representative skill set across drivers with repeated model runs and an independent quality rubric. The existing matched smoke is encouraging: its JSON report records 14,411 pilot tokens versus 40,426 legacy tokens over three single-replicate synthetic-source cases. That is a narrow efficiency signal, not a general reliability estimate. Expand only after the compatibility matrix is explicit, and identify the legacy loop responsibilities to retire so that permanent dual-engine bloat does not become the outcome.

## Primary-source checks

The installed `pydantic_ai/tools.py:481` explicitly states that `Tool.from_schema` skips schema validation; this was verified against the [official Tool API documentation](https://pydantic.dev/docs/ai/api/pydantic-ai/tools/). Its behavior makes server-side argument validation an integration responsibility.

The installed `pydantic_ai/usage.py:471` explains that request limits are checked before calls and token limits after responses. [Official agent documentation](https://pydantic.dev/docs/ai/core-concepts/agent/) describes usage limits; these do not make a response-postchecked budget a preauthorization ceiling.

Pydantic's [durable execution documentation](https://pydantic.dev/docs/ai/capabilities/durable_execution/overview/) treats durability as a separate integration concern. Saving an evidence snapshot alone does not adopt a durable execution backend.

## Conclusion

The marriage is viable. JV's skills, modular Actions, graph identity/state and established egress are worth retaining; Pydantic can own typed composition and the execution loop. The current implementation proves a useful research witness, not an enterprise-standard harness. Its decisive remaining work is enforcing trust and operational contracts—not increasing default budgets or adding more tools. Address the concrete defects first, qualify recovery and provider behavior next, then generalize and simplify.
