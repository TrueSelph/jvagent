# ADR 0044 — Native tool-calling decision protocol

**Status**: Accepted
**Date**: 2026-09-05
**Relation**: Refines [ADR-0012](0012-skill-executive-architecture.md) (one loop, one tool surface) and [ADR-0018](0018-lean-tool-surfacing.md) (what is listed vs. callable). Supersedes the "structured JSON, not native function-calling" rationale recorded in `prompts.py` at ADR-0012 time. Audit: [`.planning/reviews/2026-09-05-orchestrator-harness-audit.md`](../reviews/2026-09-05-orchestrator-harness-audit.md).

---

## 1. Context

Since ADR-0012 the Orchestrator has asked the model for **one JSON object per
tick** rendered as text (`{"action":"tool","tool":…,"args":{…}}`), listed tools
as `- name: description` prose with **no argument schema**, and replayed the
turn's tool results as a text digest inside the *user* turn. The choice was
made for speed and provider portability.

Three costs have accumulated:

1. **Argument wobble.** The model never sees argument names or types, so it
   guesses. The harness grew alias scanning and coercion to compensate
   (`_egress_exec` tries six text keys; `_coerce_plan_items` nine list keys;
   `_normalize` folds flattened calls). Each is a server-side patch for a
   contract the provider would enforce for free.
2. **Parse fragility.** Prose, code fences, multiple objects and truncation all
   read as "no decision". Anthropic has no JSON mode, so there the contract
   rested on prompt obedience alone.
3. **Weaker grounding and a wider injection surface.** Tool results in the user
   turn are indistinguishable from user text; the SAFEGUARDS reminder exists to
   patch that. Models trained on tool-use transcripts reason better over
   `tool_calls` / `tool` messages than over a digest.

Meanwhile the model layer already speaks the native contract end to end
(`tools=`, `result.tool_calls`, streaming assembly, Anthropic/Ollama role
normalisation) — the orchestrator simply passed `tools=None`. Every mainstream
harness (OpenAI Agents SDK, Anthropic tool runner, Claude Code, LangGraph,
smolagents' tool-calling agent) uses the provider's function-calling API.

## 2. Decision

Add `tool_protocol` to `OrchestratorInteractAction` with two values, **`native`
as the default**:

### 2.1 `native`

- **Tools carry schemas.** `SkillTool` gains `parameters_schema`;
  `wrap_action_tool` forwards `Tool.parameters_schema`; core, catalog and
  proactive tools declare theirs. Definitions are serialised in the OpenAI
  function shape (every provider action maps it); names outside
  `^[a-zA-Z0-9_-]{1,64}$` are aliased for the wire and mapped back;
  descriptions are capped at 1024 characters.
- **The decision is the provider's response.** A `tool_calls` entry becomes
  `{"action":"tool", …}`; plain text becomes the `reply` tool call (or `final`
  on the finalize tick, which offers no tools). No `response_format`.
- **One call per tick** (SPEC §3.3 invariant 1) is requested at the provider
  (`parallel_tool_calls=False`; Anthropic `disable_parallel_tool_use`). Extra
  calls a provider returns anyway are queued and dispatched on the following
  ticks — without a model round-trip — so every call the model made gets a
  result in the transcript.
- **The transcript is real.** This turn's steps replay as an assistant message
  carrying the `tool_calls` entry plus a `tool` message with the size-bounded
  result (the same count/size caps as the JSON digest). The loop stamps the
  provider's call id — and the call as the model made it — onto the first
  observation each decision produced, so a guard note that stood in for a
  dispatch is replayed as that call's result. Prose the model emitted with a
  call rides as assistant `content`; a deflected text reply replays as the
  model's text followed by the harness note. Server-generated notes (prep,
  seeds, guards without a call) replay as `[harness note]` user messages.
- **Prompt variants.** The protocol paragraph, user-turn template, safeguards
  reminder, finalize prompt and no-decision nudge each have a native variant.
  A persisted prompt override equal to the JSON-era built-in is recognised as
  "unchanged" and rendered with the protocol-correct built-in; operator
  overrides are honoured verbatim.

### 2.2 `json`

The pre-ADR-0044 behaviour, byte for byte (`render_system_prompt(protocol="json")`
reproduces the legacy text, so the measured injection-resistance and cache
results still apply). For providers or models without reliable function
calling.

### 2.3 Model faults are decisions, not silence

Independently of protocol, `_run_model` now returns typed decisions instead of
`None` for two faults the loop previously mistook for garbled output:

- `model_error` — the provider call raised (after the model layer's own
  retries). The loop retries once; a second consecutive failure ends the turn
  with `model_unavailable_text` (a new attribute), skips the finalize call and
  the `clarify_text` fallback, and records `ended_via=model_error`.
- `model_truncated` — `finish_reason` is `length`/`max_tokens` with nothing
  usable. The nudge says so, instead of the generic "not valid JSON".

## 3. Consequences

- **Behaviour change on upgrade** for every orchestrator agent: the model is
  handed native tools. Existing agents' persisted prompt defaults are
  recognised and swapped (§2.1); agents with custom `system_prompt` text that
  inlines the JSON contract should either set `tool_protocol: json` or update
  the override.
- The alias/coercion tolerance in `_normalize`, `_egress_exec` and
  `_coerce_plan_items` stays for the JSON protocol and as defence in depth; it
  should no longer be extended.
- Tests that canned model *decisions* (`_run_model` monkeypatched) are
  unaffected — the decision shape is unchanged. New tests cover definitions,
  transcript replay, alias round-trip, fault decisions and the loop end to end
  (`tests/action/orchestrator/test_native_tool_protocol.py`,
  `test_model_failure.py`).
- `tool_call_timeout` now defaults to 120 s (was 0 = unbounded) so a hung tool
  cannot hold the turn and the conversation lock indefinitely.
- `history_statement_max_chars` (default 4000) bounds each replayed prior
  statement; the loop resends history on every tick.

## 4. Alternatives considered

- **Keep JSON-in-text, add schemas to the prose listing.** Cheaper, but leaves
  parse fragility and the user-turn digest in place; the provider still cannot
  validate arguments.
- **Parallel tool dispatch.** Deferred: the transcript already groups parallel
  calls, so concurrent dispatch of non-terminal, side-effect-free tools is a
  contained follow-up (`max_concurrent_tools`).
- **Forced tool choice (`required`).** Rejected: plain text as the reply is the
  natural native ending and removes a round-trip on every conversational turn.
